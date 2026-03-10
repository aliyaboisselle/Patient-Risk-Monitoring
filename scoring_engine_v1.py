"""
Patient Wearable Risk Monitoring — Scoring Engine v1
=====================================================
Architecture Reference: Patient Wearable Risk Monitoring v1 (February 2026)

Implements:
  Acute Danger Engine (§4.1):
  - hr_max → tachy severity table
  - hr_min → brady severity table
  - % time above/below threshold → persistence factor
  - activity modifier using hr_max vs predicted max HR and avg_steps_per_min
  - Score-100 hard conditions with duration-gated evaluation
  - acute_base = max(tachy_severity, brady_severity, af_severity)

  Chronic Decompensation Engine (§4.2):
  - Resting filter: sleep_flag OR avg_steps_per_min < 10 (spec §4.2)
  - Chronic score-100 hard conditions force numeric score (not just flag floor)

  EHR Modifier Layer (§5):
  - Diagnosis multipliers for acute and chronic engines
  - CHF guardrail: sustained_AF >15 or 2+ AF → force acute score 100 (numeric)
  - Anxiety guardrail with HR-threshold overrides

  Confidence Layer (§6):
  - hr_min < 25 confidence force (RED floor minimum, consistent with Score-100 threshold)
  - Extreme condition floors for RED and YELLOW
  - RED floor brady threshold: hr_min < 25
  - YELLOW floor brady threshold: hr_min < 35 (consistent with anxiety guardrail §5)

  Escalation Policy (§8):
  - YELLOW acute ceiling enforced at 60–70 window
  - Cooldown and active-case suppression noted as production requirement

Known deviations from spec (documented):
  DEV-001 [af_status field, §3]: Spec defines af_minutes (numeric). Implementation
    uses af_status (categorical string) to enable direct severity table lookup.
    Acceptable engineering trade-off; no clinical logic is lost.

  DEV-002 [activity_state extensions, §4.1]: Spec's activity modifier table does
    not include "strength", "cardio", or "light" activity states. Implementation
    adds these as priority-0 rules evaluated before the step-count rules:
      strength → ×0.75  (high HR + low steps expected; wearable gym classification)
      cardio   → ×0.50  (stationary cardio; without this, fires high_hr_sedentary ×1.5)
      light    → ×0.75  (light activity; HR elevation expected, not concerning)
    Without cardio handling, a healthy patient on a stationary bike fires RED.
    Approved extension; should be formally added to architecture spec.

  DEV-003 [24-hour cooldown + active case suppression, §8]: Spec requires a 24-hour
    cooldown for same/lower flags and suppression when an active case exists. These
    require persistent state across scoring runs and are NOT implemented in v1.
    Required before any production deployment.

  DEV-004 [active_minutes field, §3]: Spec defines active_minutes per hourly pull.
    Implementation uses avg_steps_per_min as a proxy. The duration >10 min gate on
    Score-100 conditions cannot be fully evaluated without this field.

Data schema expected:
  hr_mean, hr_max, hr_min, hr_sample_count
  avg_steps_per_min
  af_status  (categorical: none|brief|two_plus|sustained_5|sustained_15)
  hrv_rmssd_daily   (nightly HRV, broadcast to all hours of that day)
  sleep_flag, confidence, true_acute_event, true_chronic_event

Usage:
  python3 scoring_engine_v1.py --data patient_data_v1.json --hour 360
  python3 scoring_engine_v1.py --data patient_data_v1.json --hour 360 --patient CF_001
  python3 scoring_engine_v1.py --data patient_data_v1.json --hour 360 --all --output results.json
  python3 scoring_engine_v1.py --data patient_data_v1.json --hour 360 --cohort chf --quiet

Dual implementation warning:
  The validation dashboard (validation_dashboard.html) contains a JavaScript
  reimplementation of this engine. The two are NOT automatically kept in sync.
  When making changes here, the following JS functions must be manually updated
  to match:
    tachySev(), bradySev(), afSev3()        ← severity tables        §4.1
    tachyPersist(), bradyPersist()          ← persistence factors    §4.1
    actModV3()                              ← activity modifier      §4.1
    scorePatientHour() s100 block           ← score-100 conditions   §4.1
    computeChronicSignals(), score-100 block← chronic engine         §4.2
    dxMod()                                 ← DX modifiers           §5
    CHF/anxiety guardrail blocks            ← guardrails             §5
    confidence force (hr_min<25, hr_max>200)← confidence force       §6
    RED/YELLOW floor blocks                 ← floor conditions       §6
    flag resolution thresholds             ← escalation policy      §8
  After updating both files, update the "Last known sync" comment in the
  dashboard JS scoring block to reflect the current RULESET version.
"""

import json
import argparse
import sys
from collections import defaultdict

RULESET = "v1.0"


# ══════════════════════════════════════════════════════════════════════════════
#  ACUTE ENGINE — SEVERITY TABLES
# ══════════════════════════════════════════════════════════════════════════════

def tachy_severity(hr_max):
    """Population safety threshold — max HR in window."""
    if hr_max < 120:  return 0
    if hr_max <= 140: return 40
    if hr_max <= 160: return 60
    if hr_max <= 180: return 80
    return 95

def brady_severity(hr_min):
    """Population safety threshold — min HR in window."""
    if hr_min > 50:  return 0
    if hr_min >= 45: return 30
    if hr_min >= 40: return 60
    if hr_min >= 30: return 85
    return 95

def af_severity(af_status, hr_max, hr_min):
    """
    AF severity. Combined AF+HR conditions take precedence.
    """
    if af_status == "none":         return 0
    if af_status != "none" and hr_max > 140: return 95   # hemodynamic risk
    if af_status != "none" and hr_min < 40:  return 95   # critical instability
    if af_status == "brief":         return 40
    if af_status == "two_plus":      return 60
    if af_status == "sustained_5":   return 75
    if af_status == "sustained_15":  return 90
    return 40


# ── PERSISTENCE FACTOR ────────────────────────────────────────────────────────

def tachy_persistence(hr_mean, hr_sample_count):
    """
    Estimates % time above tachy threshold from hr_mean (§4.1 persistence table).

    The spec calls for % time above threshold, which requires active_minutes
    data (DEV-004). Without that field, hr_mean is the best available proxy
    for what most of the hour looked like. hr_max was previously used here but
    is the single peak reading — it overstates persistence because one spike
    doesn't mean sustained elevation. hr_mean is a more representative estimate.

    Mapping: if mean HR is in severity range, most readings were likely there.
    """
    if hr_mean < 120: return 0.9    # mean below threshold → mostly normal
    if hr_mean < 130: pct_est = 0.08
    elif hr_mean < 150: pct_est = 0.15
    elif hr_mean < 165: pct_est = 0.30
    elif hr_mean < 180: pct_est = 0.45
    else:               pct_est = 0.60

    if hr_sample_count < 5:
        pct_est *= 0.8  # sparse data → conservative

    if pct_est < 0.05: return 0.9
    if pct_est < 0.20: return 1.0
    if pct_est < 0.50: return 1.2
    return 1.4

def brady_persistence(hr_mean, hr_sample_count):
    """
    Estimates % time below brady threshold from hr_mean (§4.1 persistence table).

    Same rationale as tachy_persistence — hr_min is the single lowest reading
    in the hour, not a percentage. Using hr_min caused persistence 1.2-1.4 to
    fire routinely during sleep when only one reading dipped low. hr_mean gives
    a more honest estimate of how much of the hour was actually bradycardic.
    """
    if hr_mean > 50:   return 0.9   # mean above threshold → mostly normal
    if hr_mean > 47:   pct_est = 0.08
    elif hr_mean > 43: pct_est = 0.18
    elif hr_mean > 35: pct_est = 0.38
    else:               pct_est = 0.65

    if hr_sample_count < 5:
        pct_est *= 0.8

    if pct_est < 0.05: return 0.9
    if pct_est < 0.20: return 1.0
    if pct_est < 0.50: return 1.2
    return 1.4


# ── ACTIVITY MODIFIER ────────────────────────────────────────────────────────

def activity_modifier(hr_max, hr_min, hr_mean, avg_steps, predicted_max_hr, sleep_flag,
                      activity_state=None):
    """
    Returns (modifier_value, label).

    Rules in priority order:
    0a. activity_state == "strength"                   → 0.75 (strength_training)
        Wearable workout classification — high HR + low steps is expected.
        Evaluated before all other rules to prevent false escalation.
    0b. activity_state == "cardio"                     → 0.5  (cardio_exercise)
        Wearable cardio classification — high HR + low steps is expected
        (stationary bike, rowing, elliptical). Treated as vigorous exercise.
        Without this, cardio sessions fire high_hr_sedentary (×1.5) — a 3×
        error vs the correct vigorous modifier (×0.5).
    0c. activity_state == "light"                      → 0.75 (light_activity)
        Light activity — HR elevation is expected, not concerning.
    1.  max_HR >200 AND steps <50                      → 1.5  (high_hr_sedentary_extreme)
    2.  max_HR >85% predicted AND steps >150           → 0.5  (vigorous_exercise)
    3.  max_HR >85% predicted AND 100 < steps ≤ 150   → 0.75 (moderate_exercise)
    4.  max_HR >85% predicted AND steps <50            → 1.5  (high_hr_sedentary)
    5.  max_HR >190 AND steps <100                     → 1.5  (extreme_tachycardia_rest)
    6.  hr_mean <50 AND steps <10  (rest modifier)     → 0.75 (brady_rest)
        Uses hr_mean not hr_min — hr_min is the single lowest reading and fires
        this modifier whenever one sample dips below 50, even during otherwise
        normal hours. hr_mean is more representative of the full hour.
    7.  Default                                        → 1.0
    """
    # Rule 0: wearable activity classification takes priority over step-count rules
    if activity_state == "strength":
        return 0.75, "strength_training"
    if activity_state == "cardio":
        return 0.5, "cardio_exercise"
    if activity_state == "light":
        return 0.75, "light_activity"

    pct_max = hr_max / predicted_max_hr if predicted_max_hr > 0 else 0

    if hr_max > 200 and avg_steps < 50:
        return 1.5, "high_hr_sedentary_extreme"
    if pct_max > 0.85 and avg_steps > 150:
        return 0.5, "vigorous_exercise"
    if pct_max > 0.85 and avg_steps > 100:
        return 0.75, "moderate_exercise"
    if pct_max > 0.85 and avg_steps < 50:
        return 1.5, "high_hr_sedentary"
    if hr_max > 190 and avg_steps < 100:
        return 1.5, "extreme_tachycardia_rest"
    if hr_mean < 50 and avg_steps < 10:
        return 0.75, "brady_rest"
    return 1.0, "normal"


# ── SCORE-100 HARD CONDITIONS ─────────────────────────────────────────────────

def check_score100(hr_max, hr_min, avg_steps, af_status):
    """
    Returns (fired, reason).
    These bypass formula scoring entirely → acute_base forced to 100.

    NOTE [DEV-004]: Spec gates hr_max>200 conditions on duration>10 or >15 min
    (active_minutes field). active_minutes is not present in v1 data schema;
    avg_steps_per_min is used as a proxy. Duration gating is therefore approximate.
    """
    # Combined conditions checked first (most specific → least specific)
    if hr_max > 200 and af_status != "none":
        return True, "hr_max>200+AF"
    if hr_max > 200 and avg_steps < 50:
        return True, "hr_max>200+steps<50"
    if hr_max > 200:
        return True, "hr_max>200"
    if hr_min < 25:
        return True, "hr_min<25_critical_brady"
    if hr_max < 30:
        return True, "hr_max<30_extreme_brady"
    if hr_min > 180:
        return True, "hr_min>180_sustained_extreme_tachy"
    return False, None


# ── ANXIETY GUARDRAIL ─────────────────────────────────────────────────────────

def anxiety_guardrail(hr_max, hr_min, af_status, diagnoses, score):
    """
    §5 Guardrails for anxiety: HR > 190 → force ≥90 score; HR < 35 → force ≥90;
    AF + HR > 160 sustained → force ≥90.
    These guardrails apply even when anxiety would otherwise reduce the score
    (anxiety DX multiplier = 0.85). They prevent the anxiety downgrade from
    masking genuinely critical vitals.
    """
    if "anxiety" not in diagnoses:
        return score, None
    if hr_max > 190:
        return max(score, 90), "anxiety_guardrail:hr_max>190"
    if hr_min < 35:
        return max(score, 90), "anxiety_guardrail:hr_min<35"
    if af_status != "none" and hr_max > 160:
        return max(score, 90), "anxiety_guardrail:AF+hr_max>160"
    return score, None


# ── CHF GUARDRAIL ─────────────────────────────────────────────────────────────

def chf_guardrail_acute(af_status, diagnoses, score):
    """
    §5 Guardrails for CHF: sustained AF > 15 or 2+ AF → force acute score to 100.
    This forces the NUMERIC score to 100, not merely a flag floor.
    CHF + AF is a hemodynamically unstable combination requiring immediate escalation.
    """
    if "chf" not in diagnoses:
        return score, None
    if af_status == "sustained_15":
        return 100.0, "chf_guardrail:sustained_AF>15"
    if af_status == "two_plus":
        return 100.0, "chf_guardrail:2+AF+CHF"
    return score, None


def dx_modifier(diagnoses, engine):
    """
    Returns (multiplier, diagnosis_name) for the highest-risk diagnosis present.
    Multiplier tables are defined in §5 (Acute DX Modifier / Chronic DX Modifier tables).

    Anxiety logic: anxiety is a downward modifier (0.85 acute, 0.9 chronic) intended
    to reduce false escalation from sympathetic HR spikes. If any upward-modifying
    diagnosis is also present (e.g., CHF + anxiety), the upward modifier takes precedence
    and anxiety's reduction is NOT applied. A patient with CHF must never have their
    risk score reduced due to a comorbid anxiety diagnosis. The anxiety_guardrail()
    function handles the inverse: preventing anxiety's downward adjustment from masking
    genuinely critical vitals regardless of score.

    If a patient has only anxiety and no upward diagnoses, the 0.85 reduction applies.
    """
    m = ({"chf":1.25,"af_history":1.15,"anxiety":0.85}
         if engine == "acute" else
         {"chf":1.2,"af_history":1.1,"anxiety":0.9})
    if not diagnoses: return 1.0, "none"
    if all(d == "anxiety" for d in diagnoses): return 0.85, "anxiety"
    upward = [(m.get(d, 1.0), d) for d in diagnoses if d != "anxiety"]
    if not upward: return 1.0, "none"
    return max(upward, key=lambda x: x[0])


# ── FLOOR CONDITIONS ──────────────────────────────────────────────────────────

def check_red_floor(hr_max, hr_min, af_status, avg_steps, diagnoses,
                    delta_hr, pct_hrv, chron_days, rsq, rsq_days):
    """
    §6 RED floor conditions. Returns (fired: bool, reasons: list[str]).
    When confidence >= 0.2 and any condition fires, the flag cannot be below RED.

    hr_max>200 conditions use elif to prevent a single record from appending
    multiple overlapping reasons — the most specific condition is recorded only.

    Brady thresholds (updated):
      hr_min < 25 → RED floor   (critical bradycardia, consistent with Score-100 threshold)
      hr_min < 35 → YELLOW floor (see check_yellow_floor; consistent with anxiety guardrail §5)
    """
    reasons = []
    # hr_max>200 combined conditions: most specific first, then fall through
    if hr_max > 200 and af_status != "none":
        reasons.append("hr_max>200+AF")
    elif hr_max > 200 and avg_steps < 50:
        reasons.append("hr_max>200+steps<50")
    elif hr_max > 200:
        reasons.append("hr_max>200")
    if hr_min < 25:                        reasons.append("hr_min<25")
    if hr_max < 30:                        reasons.append("hr_max<30")
    if hr_min > 180:                       reasons.append("hr_min>180")
    if af_status == "sustained_15":        reasons.append("sustained_AF>15min")
    if af_status == "two_plus" and "chf" in diagnoses: reasons.append("2+AF+CHF")
    if delta_hr > 8 and chron_days > 7:    reasons.append("dHR>8+>7d")
    if pct_hrv > 20 and chron_days > 10:   reasons.append("HRV>20%+>10d")
    if rsq > 0.8 and rsq_days > 5:        reasons.append("R2>0.8+>5d")
    return len(reasons) > 0, reasons

def check_yellow_floor(hr_max, hr_min, af_status, avg_steps, diagnoses,
                       delta_hr, pct_hrv, chron_days, rsq, rsq_days):
    """
    §6 YELLOW floor conditions. Returns (fired: bool, reasons: list[str]).
    When confidence >= 0.2 and any condition fires, the flag cannot be below YELLOW.
    YELLOW floor is suppressed if flag is already RED or CRITICAL.

    Brady thresholds (updated):
      hr_min < 35 → YELLOW floor  (consistent with anxiety guardrail §5)
      hr_min < 25 → RED floor     (see check_red_floor)
    """
    reasons = []
    if hr_max > 180 and avg_steps < 50:    reasons.append("hr_max>180+steps<50")
    if hr_max > 180 and af_status!="none": reasons.append("hr_max>180+AF")
    if hr_min < 35:                        reasons.append("hr_min<35")
    if af_status == "sustained_5":         reasons.append("sustained_AF>5min")
    if af_status == "two_plus" and "chf" in diagnoses: reasons.append("2+AF+CHF")
    if delta_hr > 5 and chron_days > 5:    reasons.append("dHR>5+>5d")
    if pct_hrv > 15 and chron_days > 7:    reasons.append("HRV>15%+>7d")
    if rsq > 0.6 and rsq_days > 5:        reasons.append("R2>0.6+>5d")
    return len(reasons) > 0, reasons


# ══════════════════════════════════════════════════════════════════════════════
#  CHRONIC ENGINE  (unchanged from v2 — uses hrv_rmssd_daily and hr_mean)
# ══════════════════════════════════════════════════════════════════════════════

def compute_chronic_signals(hourly, hour_idx, baseline_rhr, baseline_hrv):
    """
    §4.2: Chronic Decompensation Engine.
    Resting filter uses sleep_flag OR avg_steps_per_min < 10, per spec.
    Both conditions qualify as resting state for baseline computation.
    """
    resting = [
        h for h in hourly[:hour_idx + 1]
        if (h.get("sleep_flag") or h.get("avg_steps_per_min", 999) < 10)
        and h["confidence"]["score"] > 0.3
    ]
    if len(resting) < 24:
        return {"delta_hr":0,"pct_hrv":0,"chron_days":0,"rsq":0.0,"rsq_days":0,
                "baseline_available":False}

    by_day = defaultdict(list)
    for r in resting: by_day[r["day"]].append(r)
    days = sorted(by_day)
    if len(days) < 14:
        return {"delta_hr":0,"pct_hrv":0,"chron_days":0,"rsq":0.0,"rsq_days":0,
                "baseline_available":False}

    b_days = days[:14]
    b_hrs  = sorted(h["hr_mean"] for d in b_days for h in by_day[d])
    # Use hrv_rmssd_daily for chronic HRV; fall back to hrv_rmssd if needed
    b_hrvs = sorted(
        h.get("hrv_rmssd_daily") or h.get("hrv_rmssd") or baseline_hrv
        for d in b_days for h in by_day[d]
    )
    rhr_base = b_hrs[len(b_hrs) // 2]
    hrv_base = b_hrvs[len(b_hrvs) // 2]

    r_days  = days[-7:]
    r_hrs   = [h["hr_mean"] for d in r_days for h in by_day[d]]
    r_hrvs  = [h.get("hrv_rmssd_daily") or h.get("hrv_rmssd") or baseline_hrv
               for d in r_days for h in by_day[d]]
    cur_rhr = sum(r_hrs) / len(r_hrs)
    cur_hrv = sum(r_hrvs) / len(r_hrvs)

    delta_hr = max(0, cur_rhr - rhr_base)
    pct_hrv  = max(0, (hrv_base - cur_hrv) / max(hrv_base, 1) * 100)

    sustained = 0
    for d in reversed(days):
        mean = sum(h["hr_mean"] for h in by_day[d]) / len(by_day[d])
        if mean > rhr_base + 3: sustained += 1
        else: break

    rsq = 0.0; rsq_days = 0
    if len(days) >= 5:
        means = [sum(h["hr_mean"] for h in by_day[d]) / len(by_day[d]) for d in days]
        n = len(means); xm = (n - 1) / 2; ym = sum(means) / n
        ss_tot = sum((y - ym) ** 2 for y in means)
        denom  = sum((i - xm) ** 2 for i in range(n))
        if ss_tot > 0 and denom > 0:
            slope  = sum((i - xm) * (y - ym) for i, y in enumerate(means)) / denom
            y_pred = [ym + slope * (i - xm) for i in range(n)]
            ss_res = sum((y - yp) ** 2 for y, yp in zip(means, y_pred))
            rsq    = max(0.0, 1 - ss_res / ss_tot)
            rsq_days = n if slope > 0 else 0

    return {
        "delta_hr": round(delta_hr, 2), "pct_hrv": round(pct_hrv, 2),
        "chron_days": sustained, "rsq": round(rsq, 3), "rsq_days": rsq_days,
        "baseline_available": True,
        "_rhr_base": round(rhr_base, 1), "_hrv_base": round(hrv_base, 1),
        "_cur_rhr":  round(cur_rhr, 1),  "_cur_hrv":  round(cur_hrv, 1),
    }

def chronic_rhr_sev(d):
    """
    Resting HR drift severity score. §4.2 ΔHR severity table.
    d = delta between current 7-day resting mean and 14-day baseline (bpm).
    """
    return 0 if d<3 else 25 if d<5 else 45 if d<8 else 65 if d<10 else 80

def chronic_hrv_sev(p):
    """
    HRV degradation severity score. §4.2 %ΔHRV severity table.
    p = percent decline in HRV from baseline (positive = degradation).
    """
    return 0 if p<5 else 30 if p<15 else 60

def chronic_persist(days):
    """
    Persistence multiplier for sustained abnormal resting HR. §4.2 persistence table.
    days = consecutive days of above-baseline resting HR (mean > rhr_base + 3 bpm).
    """
    return 0.75 if days<5 else 1.0 if days<7 else 1.25 if days<=14 else 1.5


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN SCORING FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

FLAG_ORDER = ["GREEN", "YELLOW", "RED", "CRITICAL", "INSUFFICIENT"]


def _score_chronic(cs, diagnoses, log, rules):
    """
    Compute the pre-confidence chronic risk score from already-computed chronic signals.
    Returns (chron_pre, chronic_score100, chronic_score100_reason).

    §4.2 hard conditions force the numeric score to 100 directly; this is not
    merely a flag floor — it ensures the confidence multiplier applied later still
    yields the correct final risk value.
    """
    dhr    = cs["delta_hr"]
    pct_hrv = cs["pct_hrv"]
    c_days  = cs["chron_days"]
    rsq     = cs["rsq"]
    rsq_d   = cs["rsq_days"]

    # Score-100 hard conditions (§4.2)
    if dhr > 8 and c_days > 7:
        reason = "dHR>8+>7d"
        log.append(f"  CHRONIC SCORE-100: {reason}")
        rules.append(f"chronic_score100:{reason}")
        return 100.0, True, reason
    if pct_hrv > 20 and c_days > 10:
        reason = "HRV>20%+>10d"
        log.append(f"  CHRONIC SCORE-100: {reason}")
        rules.append(f"chronic_score100:{reason}")
        return 100.0, True, reason
    if rsq > 0.8 and rsq_d > 5:
        reason = "R2>0.8+>5d"
        log.append(f"  CHRONIC SCORE-100: {reason}")
        rules.append(f"chronic_score100:{reason}")
        return 100.0, True, reason

    # Formula path
    rhr_sev   = chronic_rhr_sev(dhr)
    hrv_sev   = chronic_hrv_sev(pct_hrv)
    chron_raw = 0.55 * rhr_sev + 0.45 * hrv_sev
    r2_bonus  = rsq > 0.4 and rsq_d >= 5
    if r2_bonus:
        chron_raw += 10
        rules.append("R2_trend_bonus")

    cp        = chronic_persist(c_days)
    chron_ta  = min(100, chron_raw * cp)
    dx_c, dx_cn = dx_modifier(diagnoses, "chronic")
    chron_pre = chron_ta * dx_c
    log.append(f"  rhr_sev={rhr_sev} hrv_sev={hrv_sev} raw={chron_raw:.1f} "
               f"persist={cp:.2f} dx={dx_c:.2f}({dx_cn}) pre-conf={chron_pre:.1f}")
    return chron_pre, False, None


def _score_acute(rec, hr_max, hr_min, hr_mean, hr_samples, avg_steps, af_status,
                 max_hr_pred, sleep_flag, diagnoses, conf, log, rules):
    """
    Compute the pre-confidence acute risk score and (possibly updated) confidence.
    Returns (acute_pre, conf, s100, s100_reason).

    Applies Score-100 hard conditions, formula scoring, DX modifier, CHF guardrail,
    and anxiety guardrail in the order specified by §4.1 and §5.
    """
    s100, s100_reason = check_score100(hr_max, hr_min, avg_steps, af_status)
    if s100:
        log.append(f"  SCORE-100: {s100_reason}")
        rules.append(f"score100:{s100_reason}")
        if hr_min < 25 or hr_max > 200:
            conf = 1.0
            log.append("  Confidence forced to 1.0 (extreme HR: hr_min<25 or hr_max>200)")
            rules.append("conf_forced_1")
        acute_pre = 100.0
    else:
        # §6: hr_min < 25 forces confidence even when Score-100 didn't fire
        if hr_min < 25:
            conf = 1.0
            log.append("  Confidence forced to 1.0 (§6: hr_min<25)")
            rules.append("conf_forced_1_brady")

        ts  = tachy_severity(hr_max)
        bs  = brady_severity(hr_min)
        afs = af_severity(af_status, hr_max, hr_min)
        acute_base = max(ts, bs, afs)

        tp = tachy_persistence(hr_mean, hr_samples)
        bp = brady_persistence(hr_mean, hr_samples)
        persistence = tp if (ts >= bs and ts >= afs) else bp

        am, am_label = activity_modifier(hr_max, hr_min, hr_mean, avg_steps,
                                         max_hr_pred, sleep_flag,
                                         activity_state=rec.get("activity_state"))
        acute_pre = min(100, acute_base * persistence * am)
        log.append(f"  tachy_sev={ts} brady_sev={bs} af_sev={afs} "
                   f"base={acute_base} persist={persistence:.2f} "
                   f"act={am:.2f}({am_label}) → acute_pre={acute_pre:.1f}")

    dx_a, dx_an = dx_modifier(diagnoses, "acute")
    acute_pre *= dx_a
    log.append(f"  dx={dx_a:.2f}({dx_an}) → pre-conf={acute_pre:.1f}")

    # §5 CHF guardrail: forces numeric acute score to 100
    acute_pre, chf_g = chf_guardrail_acute(af_status, diagnoses, acute_pre)
    if chf_g:
        log.append(f"  CHF GUARDRAIL (numeric force): {chf_g}")
        rules.append(chf_g)

    # §5 Anxiety guardrail: prevents anxiety downgrade from masking critical vitals
    acute_pre, ag = anxiety_guardrail(hr_max, hr_min, af_status, diagnoses, acute_pre)
    if ag:
        log.append(f"  ANXIETY GUARDRAIL: {ag}")
        rules.append(ag)

    return acute_pre, conf, s100, s100_reason


def _resolve_flag(acute_risk, chronic_risk, conf, red_f, red_r, yel_f, yel_r, log):
    """
    Apply §8 escalation policy to determine final risk flag.
    Returns (flag, floor_applied).

    YELLOW ceiling is explicitly enforced: acute_risk must be in [60, 70) to
    produce YELLOW from formula alone — values ≥70 produce RED.
    Floor conditions can elevate a GREEN → YELLOW or GREEN/YELLOW → RED
    provided confidence ≥ 0.2. INSUFFICIENT fires when conf < 0.2 and a
    RED-floor condition is active.
    """
    floor_applied = None

    if red_f and conf < 0.2:
        log.append("  INSUFFICIENT: conf<0.2 + red-floor active")
        return "INSUFFICIENT", None

    if   acute_risk > 85 and conf > 0.6:                         flag = "CRITICAL"
    elif acute_risk > 85:                                         flag = "RED"
    elif acute_risk >= 70 or chronic_risk > 80:                   flag = "RED"
    elif 60 <= acute_risk < 70 or (60 <= chronic_risk <= 80):     flag = "YELLOW"
    else:                                                          flag = "GREEN"

    if red_f and conf >= 0.2 and FLAG_ORDER.index(flag) < FLAG_ORDER.index("RED"):
        flag = "RED"
        floor_applied = f"RED:{red_r[0]}"
        log.append(f"  RED floor: {red_r[0]}")

    if yel_f and conf >= 0.2 and flag not in ("RED", "CRITICAL"):
        if FLAG_ORDER.index(flag) < FLAG_ORDER.index("YELLOW"):
            flag = "YELLOW"
            floor_applied = f"YELLOW:{yel_r[0]}"
            log.append(f"  YELLOW floor: {yel_r[0]}")

    return flag, floor_applied


def score_patient_hour(patient, hour_idx):
    """
    Main entry point. Orchestrates chronic engine → acute engine → risk calculation
    → floor application → flag resolution for a single patient at a single hour.

    Returns a fully populated result dict including scores, flag, audit_log, and
    ground truth for downstream validation (§9).
    """
    static = patient["static"]
    hourly = patient["hourly"]
    rec    = hourly[hour_idx]

    diagnoses   = static["diagnoses"]
    max_hr_pred = static["predicted_max_hr"]
    base_rhr    = static["baseline_rhr"]
    base_hrv    = static["baseline_hrv"]

    # ── Extract hourly fields ─────────────────────────────────────────────
    hr_mean    = rec.get("hr_mean",  rec.get("hr", 0))
    hr_max     = rec.get("hr_max",   hr_mean)
    hr_min     = rec.get("hr_min",   hr_mean)
    hr_samples = rec.get("hr_sample_count", 8)
    avg_steps  = rec.get("avg_steps_per_min", rec.get("steps_per_min", 0))
    af_status  = rec.get("af_status", "none")
    sleep_flag = rec.get("sleep_flag", False)
    hrv        = rec.get("hrv_rmssd_daily", rec.get("hrv", base_hrv))
    conf       = rec["confidence"]["score"]

    log   = []
    rules = []

    # ── Chronic engine (§4.2) ─────────────────────────────────────────────
    cs = compute_chronic_signals(hourly, hour_idx, base_rhr, base_hrv)
    log.append("=== CHRONIC ENGINE ===")
    if not cs["baseline_available"]:
        log.append("  Baseline not yet established (<14 days resting data)")
    else:
        log.append(f"  RHR base={cs['_rhr_base']} cur={cs['_cur_rhr']} delta={cs['delta_hr']}")
        log.append(f"  HRV base={cs['_hrv_base']} cur={cs['_cur_hrv']} pct={cs['pct_hrv']:.1f}%")
        log.append(f"  Sustained={cs['chron_days']}d  R²={cs['rsq']:.3f}  R²_days={cs['rsq_days']}")
    chron_pre, chronic_score100, chronic_score100_reason = _score_chronic(cs, diagnoses, log, rules)

    # ── Acute engine (§4.1) ───────────────────────────────────────────────
    log.append("=== ACUTE ENGINE ===")
    log.append(f"  hr_mean={hr_mean} hr_max={hr_max} hr_min={hr_min} "
               f"samples={hr_samples} af={af_status} steps={avg_steps} sleep={sleep_flag}")
    acute_pre, conf, s100, s100_reason = _score_acute(
        rec, hr_max, hr_min, hr_mean, hr_samples, avg_steps, af_status,
        max_hr_pred, sleep_flag, diagnoses, conf, log, rules)

    # ── Risk scores (§7, capped at 100 after confidence) ──────────────────
    log.append("=== RISK ===")
    acute_risk   = min(100, acute_pre  * conf)
    chronic_risk = min(100, chron_pre  * conf)
    log.append(f"  acute: {acute_pre:.1f} × {conf:.3f} = {acute_risk:.1f}")
    log.append(f"  chronic: {chron_pre:.1f} × {conf:.3f} = {chronic_risk:.1f}")

    # ── Floor conditions (§6) ─────────────────────────────────────────────
    dhr   = cs["delta_hr"]; pct_hrv = cs["pct_hrv"]
    c_days = cs["chron_days"]; rsq = cs["rsq"]; rsq_d = cs["rsq_days"]
    red_f, red_r = check_red_floor(
        hr_max, hr_min, af_status, avg_steps, diagnoses,
        dhr, pct_hrv, c_days, rsq, rsq_d)
    yel_f, yel_r = check_yellow_floor(
        hr_max, hr_min, af_status, avg_steps, diagnoses,
        dhr, pct_hrv, c_days, rsq, rsq_d)

    # ── Flag resolution (§8) ──────────────────────────────────────────────
    log.append("=== FLAG ===")
    flag, floor_applied = _resolve_flag(
        acute_risk, chronic_risk, conf, red_f, red_r, yel_f, yel_r, log)
    log.append(f"  FINAL: {flag}")

    return {
        "patient_id":   patient["patient_id"],
        "cohort":       patient["cohort"],
        "hour_idx":     hour_idx,
        "timestamp":    rec["timestamp"],
        "day":          rec["day"],
        "hour":         rec["hour"],
        "ruleset":      RULESET,
        "inputs": {
            "hr_mean": hr_mean, "hr_max": hr_max, "hr_min": hr_min,
            "hr_sample_count": hr_samples,
            "avg_steps_per_min": avg_steps,
            "af_status": af_status, "sleep_flag": sleep_flag,
            "hrv_rmssd_daily": hrv,
            "conf": round(conf, 3), "diagnoses": diagnoses,
            "age": static["age"],
            "activity_state": rec.get("activity_state", "unknown"),
        },
        "chronic_signals": {
            "delta_hr": dhr, "pct_hrv": pct_hrv, "chron_days": c_days,
            "rsq": rsq, "rsq_days": rsq_d,
            "baseline_available": cs["baseline_available"],
            "chronic_score100": chronic_score100,
            "chronic_score100_reason": chronic_score100_reason,
            "_rhr_base": cs.get("_rhr_base"), "_hrv_base": cs.get("_hrv_base"),
            "_cur_rhr":  cs.get("_cur_rhr"),  "_cur_hrv":  cs.get("_cur_hrv"),
        },
        "scores": {
            "acute_pre_conf":   round(acute_pre,   2),
            "chronic_pre_conf": round(chron_pre,   2),
            "acute_risk":       round(acute_risk,  2),
            "chronic_risk":     round(chronic_risk, 2),
            "confidence":       round(conf, 3),
        },
        "acute_breakdown": {
            "tachy_sev":       tachy_severity(hr_max) if not s100 else 100,
            "brady_sev":       brady_severity(hr_min) if not s100 else 100,
            "af_sev":          af_severity(af_status, hr_max, hr_min) if not s100 else 100,
            "score100":        s100,
            "score100_reason": s100_reason,
        },
        "flag":            flag,
        "flag_source":     "floor" if floor_applied else "formula",
        "floor_applied":   floor_applied,
        "red_floors":      red_r,
        "yellow_floors":   yel_r,
        "rules_triggered": rules,
        "ground_truth": {
            "true_acute":   rec.get("true_acute_event",  None),
            "true_chronic": rec.get("true_chronic_event", None),
        },
        "audit_log": log,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def print_result(r, verbose=True):
    sym = {"GREEN":"🟢","YELLOW":"🟡","RED":"🔴",
           "CRITICAL":"🚨","INSUFFICIENT":"⚫"}.get(r["flag"], "?")
    inp = r["inputs"]; s = r["scores"]; cs = r["chronic_signals"]
    gt  = r["ground_truth"]; ab = r["acute_breakdown"]

    print(f"\n{sym} {r['flag']}  |  {r['patient_id']}  |  "
          f"Hour {r['hour_idx']} (Day {r['day']}, {r['hour']:02d}:00)  |  {r['timestamp']}")
    print(f"   Cohort: {r['cohort']:<18}  Age: {inp['age']}  "
          f"Dx: {inp['diagnoses'] or 'none'}")
    print(f"   HR mean={inp['hr_mean']}  max={inp['hr_max']}  min={inp['hr_min']}  "
          f"samples={inp['hr_sample_count']}")
    print(f"   AF: {inp['af_status']:<14}  Steps/min: {inp['avg_steps_per_min']}  "
          f"Sleep: {inp['sleep_flag']}  State: {inp['activity_state']}")
    print(f"   Conf: {inp['conf']:.3f}   HRV: {inp['hrv_rmssd_daily']}")
    print(f"   Acute breakdown → tachy={ab['tachy_sev']}  brady={ab['brady_sev']}  "
          f"af={ab['af_sev']}  score100={ab['score100']}")
    print(f"   Scores → acute={s['acute_risk']:.1f} (pre={s['acute_pre_conf']:.1f})  "
          f"chronic={s['chronic_risk']:.1f} (pre={s['chronic_pre_conf']:.1f})")
    if cs["baseline_available"]:
        print(f"   Chronic → ΔHR={cs['delta_hr']:.1f}  %ΔHRV={cs['pct_hrv']:.1f}%  "
              f"days={cs['chron_days']}  R²={cs['rsq']:.3f}")
    if r["floor_applied"]:
        print(f"   ⚡ Floor: {r['floor_applied']}")
    if r["rules_triggered"]:
        print(f"   Rules: {', '.join(r['rules_triggered'])}")
    if gt["true_acute"] is not None:
        print(f"   GT: acute={gt['true_acute']}  chronic={gt['true_chronic']}")
    if verbose:
        print("\n   -- Audit --")
        for line in r["audit_log"]:
            print(f"   {line}")


def main():
    parser = argparse.ArgumentParser(description="Wearable Risk Scoring Engine v1")
    parser.add_argument("--data",    required=True, help="Path to patient_data_v1.json")
    parser.add_argument("--hour",    type=int, required=True, help="Hour index 0–719")
    parser.add_argument("--patient", default=None, help="Score a specific patient ID")
    parser.add_argument("--all",     action="store_true", help="Score all patients")
    parser.add_argument("--cohort",  default=None, help="Filter to a specific cohort")
    parser.add_argument("--output",  default=None, help="Write results to JSON file")
    parser.add_argument("--quiet",   action="store_true", help="Suppress per-patient audit log")
    args = parser.parse_args()

    if not (0 <= args.hour <= 719):
        print("Error: --hour must be 0–719"); sys.exit(1)

    print(f"Loading {args.data}...")
    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: data file not found: {args.data}"); sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {args.data}: {e}"); sys.exit(1)

    if "patients" not in data or not isinstance(data["patients"], list):
        print("Error: data file must contain a top-level 'patients' list"); sys.exit(1)

    patients = data["patients"]
    required_static = {"diagnoses", "predicted_max_hr", "baseline_rhr", "baseline_hrv", "age"}
    required_hourly = {"hr_mean", "hr_max", "hr_min", "confidence", "af_status"}
    for p in patients[:3]:  # Spot-check first 3 patients to avoid O(n) load cost
        missing_s = required_static - set(p.get("static", {}).keys())
        if missing_s:
            print(f"Error: patient '{p.get('patient_id','?')}' static record missing: {missing_s}")
            sys.exit(1)
        if p.get("hourly"):
            missing_h = required_hourly - set(p["hourly"][0].keys())
            if missing_h:
                print(f"Error: patient '{p.get('patient_id','?')}' hourly record missing: {missing_h}")
                sys.exit(1)

    if args.cohort:
        patients = [p for p in patients if p["cohort"] == args.cohort]
        print(f"Filtered to '{args.cohort}': {len(patients)} patients")
    if args.patient:
        patients = [p for p in patients if p["patient_id"] == args.patient]
        if not patients:
            print(f"Patient '{args.patient}' not found"); sys.exit(1)

    results = []
    counts  = {}
    for p in patients:
        r = score_patient_hour(p, args.hour)
        results.append(r)
        counts[r["flag"]] = counts.get(r["flag"], 0) + 1
        print_result(r, verbose=not args.quiet)

    if len(results) > 1:
        d = args.hour // 24 + 1; h = args.hour % 24
        print(f"\n{'='*55}")
        print(f"SUMMARY — Hour {args.hour} (Day {d}, {h:02d}:00)  [{len(results)} patients]")
        for fl in ["CRITICAL","RED","YELLOW","GREEN","INSUFFICIENT"]:
            sym = {"GREEN":"🟢","YELLOW":"🟡","RED":"🔴",
                   "CRITICAL":"🚨","INSUFFICIENT":"⚫"}[fl]
            if counts.get(fl, 0):
                print(f"  {sym} {fl:<14}: {counts[fl]}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
