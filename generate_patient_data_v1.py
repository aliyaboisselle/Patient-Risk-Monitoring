"""
Patient Wearable Risk Monitoring — Synthetic Data Generator v1
==============================================================

Output: patient_data_v3.json
"""

import json, random, math
from datetime import datetime, timedelta

random.seed(42)

# ── CONFIG ────────────────────────────────────────────────────────────────────
DAYS        = 30
HPD         = 24
TOTAL_HOURS = DAYS * HPD   # 720
START       = datetime(2026, 1, 1)

COHORTS = {
    "healthy_baseline": 30,
    "anxiety":          20,
    "chf":              20,
    "af_prone":         20,
    "mixed_risk":       10,
}
PREFIX = {
    "healthy_baseline": "HB",
    "anxiety":          "AX",
    "chf":              "CF",
    "af_prone":         "AF",
    "mixed_risk":       "MX",
}

AF_STATES = ["none", "brief", "two_plus", "sustained_5", "sustained_15"]


# ── HELPERS ───────────────────────────────────────────────────────────────────

def clamp(v, lo, hi):  return max(lo, min(hi, v))
def noise(sd):         return random.gauss(0, sd)
def ramp(cur, tgt, a=0.35): return cur + (tgt - cur) * a


# ── ACTIVITY STATE ────────────────────────────────────────────────────────────

# Per-hour state probabilities: (sleep, sedentary, light, cardio, strength)
HOUR_STATE_PROB = {
    0:  (0.95, 0.04, 0.01, 0.00, 0.00),
    1:  (0.96, 0.03, 0.01, 0.00, 0.00),
    2:  (0.97, 0.02, 0.01, 0.00, 0.00),
    3:  (0.97, 0.02, 0.01, 0.00, 0.00),
    4:  (0.95, 0.04, 0.01, 0.00, 0.00),
    5:  (0.85, 0.12, 0.03, 0.00, 0.00),
    6:  (0.55, 0.30, 0.12, 0.02, 0.01),
    7:  (0.20, 0.40, 0.30, 0.08, 0.02),
    8:  (0.05, 0.45, 0.35, 0.10, 0.05),
    9:  (0.02, 0.50, 0.33, 0.10, 0.05),
    10: (0.02, 0.52, 0.33, 0.08, 0.05),
    11: (0.02, 0.50, 0.33, 0.10, 0.05),
    12: (0.02, 0.40, 0.45, 0.10, 0.03),
    13: (0.05, 0.48, 0.33, 0.10, 0.04),
    14: (0.03, 0.52, 0.30, 0.10, 0.05),
    15: (0.02, 0.50, 0.30, 0.13, 0.05),
    16: (0.02, 0.40, 0.30, 0.20, 0.08),
    17: (0.02, 0.30, 0.30, 0.28, 0.10),
    18: (0.02, 0.32, 0.35, 0.25, 0.06),
    19: (0.03, 0.42, 0.38, 0.13, 0.04),
    20: (0.05, 0.55, 0.33, 0.05, 0.02),
    21: (0.15, 0.60, 0.22, 0.02, 0.01),
    22: (0.50, 0.40, 0.09, 0.01, 0.00),
    23: (0.80, 0.17, 0.03, 0.00, 0.00),
}

def sample_state(hour, cohort):
    p = list(HOUR_STATE_PROB[hour])
    if cohort in ("chf", "mixed_risk"):
        bump = p[3] * 0.6 + p[4] * 0.6
        p[3] *= 0.4; p[4] *= 0.4; p[1] += bump
    if cohort == "healthy_baseline":
        p[3] = min(0.35, p[3] * 1.15)
    total = sum(p); p = [x / total for x in p]
    r = random.random(); cumul = 0
    for k, prob in zip(["sleep","sedentary","light","cardio","strength"], p):
        cumul += prob
        if r < cumul: return k
    return "sedentary"


# ── HR MAX / MIN / MEAN / SAMPLE COUNT ───────────────────────────────────────
#
# Each state defines:
#   mean_delta_lo, mean_delta_hi  — how far mean HR sits above baseline
#   spread_lo, spread_hi          — half-width of the max-min range
#   sample_count_lo, sample_count_hi — realistic wearable sample count/hr
#
STATE_HR_PARAMS = {
    #                  mean_Δlo  mean_Δhi  spread_lo spread_hi  samp_lo samp_hi
    "sleep":     dict(mdlo=-8,   mdhi=-3,  splo=3,   sphi=6,    slo=4,  shi=8),
    "sedentary": dict(mdlo=0,    mdhi=5,   splo=5,   sphi=12,   slo=6,  shi=12),
    "light":     dict(mdlo=10,   mdhi=20,  splo=8,   sphi=18,   slo=8,  shi=15),
    "cardio":    dict(mdlo=40,   mdhi=80,  splo=15,  sphi=30,   slo=10, shi=18),
    "strength":  dict(mdlo=50,   mdhi=90,  splo=8,   sphi=20,   slo=8,  shi=14),
}

def gen_hr_triplet(state, baseline_rhr, max_hr, drift=0.0, af_state="none",
                   is_spike=False, is_acute=False, prev_mean=None):
    """
    Returns (hr_mean, hr_max, hr_min, sample_count).
    hr_mean is ramp-smoothed from prev_mean.
    hr_max and hr_min are statistically spread around hr_mean.
    """
    p = STATE_HR_PARAMS[state]

    # ── Mean HR target ────────────────────────────────────────────────────
    if state == "strength":
        # Strength: absolute range independent of baseline
        target_mean = random.uniform(150, 170)
    elif af_state != "none":
        # AF: flip between rate-controlled and rapid ventricular response
        if random.random() < 0.45:
            delta = random.uniform(10, 35)   # rate controlled
        else:
            delta = random.uniform(45, 95)   # RVR
        target_mean = baseline_rhr + drift + delta + noise(6)
    elif is_spike:
        target_mean = baseline_rhr + random.uniform(45, 90) + noise(8)
    elif is_acute:
        target_mean = baseline_rhr + drift + random.uniform(55, 105) + noise(10)
    else:
        delta = random.uniform(p["mdlo"], p["mdhi"])
        target_mean = baseline_rhr + drift + delta

    # Ramp smoothing
    if prev_mean is not None:
        hr_mean = ramp(prev_mean, target_mean, a=0.35)
    else:
        hr_mean = target_mean

    hr_mean = clamp(hr_mean + noise(3), 28, max_hr)

    # ── Spread for max / min ──────────────────────────────────────────────
    # AF adds extra spread (irregular rhythm)
    af_spread_bonus = {"none": 0, "brief": 4, "two_plus": 8,
                       "sustained_5": 12, "sustained_15": 16}.get(af_state, 0)
    half_spread = random.uniform(p["splo"], p["sphi"]) + af_spread_bonus

    hr_max = clamp(int(hr_mean + half_spread + abs(noise(4))), int(hr_mean), max_hr)
    hr_min = clamp(int(hr_mean - half_spread - abs(noise(3))), 25, int(hr_mean))

    # Strength: ensure max is in the 155-185 range
    if state == "strength":
        hr_max = clamp(hr_max, int(hr_mean), min(185, max_hr))
        hr_min = clamp(hr_min, 25, int(hr_mean))

    # Final ordering guarantee
    hr_max = max(hr_max, int(hr_mean))
    hr_min = min(hr_min, int(hr_mean))

    # Sample count
    sample_count = random.randint(p["slo"], p["shi"])

    return round(hr_mean, 1), hr_max, hr_min, sample_count


# ── STEPS ─────────────────────────────────────────────────────────────────────

STATE_STEPS = {
    "sleep":     (0,   5),
    "sedentary": (0,   60),
    "light":     (30,  120),
    "cardio":    (80,  180),
    "strength":  (0,   25),
}

def gen_steps(state):
    lo, hi = STATE_STEPS[state]
    if hi == 0: return 0
    raw = random.uniform(lo, hi)
    return clamp(int(raw + noise(raw * 0.12)), lo, hi)


# ── HRV ───────────────────────────────────────────────────────────────────────

def gen_hrv_rmssd(state, baseline_hrv, drift_penalty=0.0, af_state="none",
                  is_spike=False):
    """
    Returns hrv_rmssd (ms) during sleep hours only; None otherwise.
    Matches consumer wearable nightly HRV reporting.
    """
    if state != "sleep":
        return None

    af_pen   = {"none":0,"brief":-4,"two_plus":-10,
                "sustained_5":-16,"sustained_15":-22}.get(af_state, 0)
    spike_pen = -8 if is_spike else 0

    val = baseline_hrv + af_pen + spike_pen - drift_penalty + noise(3.5)
    return round(max(4.0, val), 1)


# ── CONFIDENCE ────────────────────────────────────────────────────────────────

def gen_confidence(cohort, in_dropout, in_partial):
    base = {"healthy_baseline":0.90,"anxiety":0.83,
            "chf":0.74,"af_prone":0.80,"mixed_risk":0.69}[cohort]
    if in_dropout:
        cov  = round(random.uniform(0.00, 0.10), 3)
        comp = round(random.uniform(0.00, 0.15), 3)
        rec  = round(random.uniform(0.10, 0.40), 3)
    elif in_partial:
        cov  = round(random.uniform(0.30, 0.65), 3)
        comp = round(random.uniform(0.30, 0.65), 3)
        rec  = round(random.uniform(0.60, 0.90), 3)
    else:
        on = random.random() < base
        if not on:
            cov  = round(random.uniform(0.00, 0.25), 3)
            comp = round(random.uniform(0.00, 0.30), 3)
            rec  = round(random.uniform(0.30, 0.60), 3)
        else:
            cov  = round(random.uniform(0.70, 1.00), 3)
            comp = round(random.uniform(0.72, 1.00), 3)
            rec  = 1.0
    raw   = cov * 0.6 + comp * 0.3 + rec * 0.1
    score = round(min(1.0, raw * 1.2), 3)
    return {"coverage_ratio": cov, "sample_completeness": comp,
            "recency_factor": rec, "score": score}


# ── DROPOUT WINDOWS ───────────────────────────────────────────────────────────

def schedule_dropout_windows(cohort):
    dropout = set(); partial = set()
    for _ in range(random.randint(2, 5)):
        s = random.randint(0, TOTAL_HOURS - 1)
        for h in range(s, min(s + random.randint(1, 6), TOTAL_HOURS)):
            dropout.add(h)
    for _ in range(random.randint(8, 20)):
        s = random.randint(0, TOTAL_HOURS - 1)
        for h in range(s, min(s + random.randint(1, 3), TOTAL_HOURS)):
            if h not in dropout: partial.add(h)
    return dropout, partial


# ── AF SCHEDULE ───────────────────────────────────────────────────────────────

def schedule_af_events(density="normal"):
    af_hours = {}; events = []
    eps_per_week = (2, 4) if density == "normal" else (3, 6)
    for week in range(DAYS // 7 + 1):
        for _ in range(random.randint(*eps_per_week)):
            day = week * 7 + random.randint(0, 6)
            if day >= DAYS: continue
            start_h = random.randint(0, 22)
            dur = clamp(int(math.exp(random.uniform(0, 2.1))), 1, 8)
            peak = (["brief","two_plus","sustained_5","sustained_15"]
                    [[0,1,2,3][min(dur-1, 3)]])
            start_idx = day * HPD + start_h
            events.append((start_idx, dur, peak))
            peak_ord = AF_STATES.index(peak)
            for offset in range(dur):
                idx = start_idx + offset
                if idx >= TOTAL_HOURS: break
                prog = offset / max(dur - 1, 1)
                if prog < 0.3:   ord_ = max(0, peak_ord - 2 + int(prog * 6))
                elif prog > 0.7: ord_ = max(0, peak_ord - int((prog - 0.7) * 6))
                else:            ord_ = peak_ord
                af_hours[idx] = AF_STATES[min(ord_, peak_ord)]
    return af_hours, events


def schedule_af_fp_hours():
    """Sporadic sensor false positives — brief, no associated HR change."""
    fp = {}
    for _ in range(random.randint(3, 10)):
        fp[random.randint(0, TOTAL_HOURS - 1)] = "brief"
    return fp


# ── CHF DRIFT (nonlinear) ─────────────────────────────────────────────────────

def build_chf_drift(n_days=DAYS):
    total = random.uniform(6, 16)
    profile = [0.0] * n_days
    remaining_days = n_days; remaining_drift = total
    n_segs = random.randint(3, 5)
    segments = []
    for i in range(n_segs):
        seg_days = max(2, remaining_days // (n_segs - i))
        if i == n_segs - 1: seg_days = remaining_days
        if random.random() < 0.45:
            seg_drift = 0.0
        else:
            frac = random.uniform(0.2, 0.7) if i < n_segs - 1 else 1.0
            seg_drift = remaining_drift * frac
            remaining_drift -= seg_drift
        segments.append((seg_days, seg_drift))
        remaining_days -= seg_days
        if remaining_days <= 0: break
    day = 0; cumul = 0.0
    for seg_days, seg_drift in segments:
        for d in range(seg_days):
            if day >= n_days: break
            prog = (d + 1) / seg_days
            sig = 1 / (1 + math.exp(-8 * (prog - 0.5)))
            profile[day] = max(0, cumul + seg_drift * sig + noise(0.05))
            day += 1
        cumul = profile[day - 1] if day > 0 else 0.0
    if random.random() < 0.4:
        step_day = random.randint(10, 25)
        step_size = random.uniform(1.5, 3.5)
        for d in range(step_day, n_days):
            profile[d] += step_size
    return [round(v, 3) for v in profile]


# ── GROUND TRUTH ──────────────────────────────────────────────────────────────

def build_ground_truth(cohort, af_events, chf_drift,
                       spike_hours=None, acute_hours=None):
    gt_acute   = [0] * TOTAL_HOURS
    gt_chronic = [0] * TOTAL_HOURS
    event_log  = []

    # Sustained AF → true acute
    for start_idx, duration, peak in af_events:
        if peak in ("sustained_5", "sustained_15"):
            for h in range(start_idx, min(start_idx + duration, TOTAL_HOURS)):
                gt_acute[h] = 1
            event_log.append({"type": "af_acute", "start_hour": start_idx,
                               "end_hour": min(start_idx+duration-1, TOTAL_HOURS-1),
                               "peak_state": peak, "day_start": start_idx//HPD+1})

    # Anxiety spikes
    if spike_hours:
        sorted_s = sorted(spike_hours)
        if sorted_s:
            seg = sorted_s[0]; prev = sorted_s[0]
            for h in sorted_s[1:] + [-999]:
                if h != prev + 1:
                    for sh in range(seg, prev + 1): gt_acute[sh] = 1
                    event_log.append({"type":"anxiety_spike","start_hour":seg,
                                      "end_hour":prev,"day_start":seg//HPD+1})
                    seg = h
                prev = h

    # Mixed acute episodes
    if acute_hours:
        sorted_a = sorted(acute_hours)
        if sorted_a:
            seg = sorted_a[0]; prev = sorted_a[0]
            for h in sorted_a[1:] + [-999]:
                if h != prev + 1:
                    for sh in range(seg, prev + 1): gt_acute[sh] = 1
                    event_log.append({"type":"acute_episode","start_hour":seg,
                                      "end_hour":prev,"day_start":seg//HPD+1})
                    seg = h
                prev = h

    # CHF chronic: drift ≥5 bpm for ≥5 consecutive days
    if chf_drift:
        above = [d >= 5.0 for d in chf_drift]
        streak = 0; chron_start = None
        for day, ab in enumerate(above):
            if ab:
                streak += 1
                if streak >= 5 and chron_start is None: chron_start = day - 4
            else:
                if chron_start is not None:
                    event_log.append({"type":"chf_chronic","start_day":chron_start+1,
                                      "end_day":day,"peak_drift":round(max(chf_drift[chron_start:day]),2)})
                streak = 0; chron_start = None
        if chron_start is not None:
            event_log.append({"type":"chf_chronic","start_day":chron_start+1,
                               "end_day":DAYS,"peak_drift":round(max(chf_drift[chron_start:]),2)})
        for day in range(DAYS):
            if chf_drift[day] >= 5.0:
                for h in range(HPD): gt_chronic[day*HPD+h] = 1

    return gt_acute, gt_chronic, event_log


# ── DAILY HRV AGGREGATION ─────────────────────────────────────────────────────

def build_daily_hrv(hourly_hrv_raw, baseline_hrv):
    """
    One nightly HRV value per day from sleep-hour readings only.
    Non-sleep hours receive None (matches wearable behavior).
    Returned as list of 30 daily values for reference.
    """
    daily = []
    for day in range(DAYS):
        sleep_vals = [
            hourly_hrv_raw[day * HPD + h]
            for h in range(HPD)
            if (h >= 22 or h < 6)
            and hourly_hrv_raw[day * HPD + h] is not None
        ]
        if sleep_vals:
            val = round(sum(sleep_vals) / len(sleep_vals), 1)
        else:
            val = round(baseline_hrv + noise(3), 1)
        daily.append(max(4.0, val))
    return daily


# ── STATIC ATTRIBUTES ─────────────────────────────────────────────────────────

def gen_static(cohort):
    if cohort == "healthy_baseline":
        age=random.randint(28,65); rhr=random.randint(55,72)
        hrv=random.uniform(45,80); dx=[]
    elif cohort == "anxiety":
        age=random.randint(22,55); rhr=random.randint(62,80)
        hrv=random.uniform(28,52); dx=["anxiety"]
    elif cohort == "chf":
        age=random.randint(52,82); rhr=random.randint(65,85)
        hrv=random.uniform(15,35); dx=["chf"]
    elif cohort == "af_prone":
        age=random.randint(45,78); rhr=random.randint(60,80)
        hrv=random.uniform(22,52); dx=["af_history"]
    else:  # mixed_risk
        age=random.randint(50,80); rhr=random.randint(68,88)
        hrv=random.uniform(12,32)
        dx=["chf"]+( ["af_history"] if random.random()<0.55 else [])
    return {"age":age,"diagnoses":dx,"baseline_rhr":rhr,
            "baseline_hrv":round(hrv,1),"predicted_max_hr":202-age}


# ── PATIENT GENERATOR ─────────────────────────────────────────────────────────

def generate_patient(patient_id, cohort):
    static   = gen_static(cohort)
    rhr      = static["baseline_rhr"]
    hrv_b    = static["baseline_hrv"]
    max_hr   = static["predicted_max_hr"]

    # ── Event schedules ───────────────────────────────────────────────────
    af_hour_map = {}; af_events = []
    spike_hours = set(); acute_hours = set(); chf_drift = None

    if cohort == "anxiety":
        for week in range(DAYS // 7 + 1):
            for _ in range(random.randint(2, 4)):
                day = week * 7 + random.randint(0, 6)
                if day >= DAYS: continue
                h_s = random.randint(8, 21); dur = random.randint(1, 3)
                for dh in range(dur): spike_hours.add(day*HPD+h_s+dh)

    elif cohort == "chf":
        chf_drift = build_chf_drift()

    elif cohort == "af_prone":
        af_hour_map, af_events = schedule_af_events(density="high")
        for h, s in schedule_af_fp_hours().items():
            if h not in af_hour_map: af_hour_map[h] = s

    elif cohort == "mixed_risk":
        chf_drift = build_chf_drift()
        af_hour_map, af_events = schedule_af_events(density="normal")
        for week in range(DAYS // 7 + 1):
            if random.random() < 0.65:
                day = week * 7 + random.randint(0, 6)
                if day >= DAYS: continue
                h_s = random.randint(8, 20); dur = random.randint(1, 4)
                for dh in range(dur): acute_hours.add(day*HPD+h_s+dh)

    dropout_set, partial_set = schedule_dropout_windows(cohort)

    gt_acute, gt_chronic, event_log = build_ground_truth(
        cohort, af_events, chf_drift,
        spike_hours=spike_hours if cohort == "anxiety" else None,
        acute_hours=acute_hours if cohort == "mixed_risk" else None,
    )

    # ── Hourly simulation ─────────────────────────────────────────────────
    raw_hrv_per_hour = []   # sleep-hours only; others None
    prev_mean_hr     = float(rhr)
    hourly           = []

    for h_idx in range(TOTAL_HOURS):
        day  = h_idx // HPD
        hour = h_idx % HPD
        ts   = START + timedelta(hours=h_idx)
        drift = chf_drift[day] if chf_drift else 0.0

        # Activity state
        if cohort == "anxiety" and h_idx in spike_hours:
            state = "sedentary"
        elif cohort == "mixed_risk" and h_idx in acute_hours:
            state = "sedentary"
        else:
            state = sample_state(hour, cohort)

        af_state  = af_hour_map.get(h_idx, "none")
        is_spike  = (cohort == "anxiety"    and h_idx in spike_hours)
        is_acute  = (cohort == "mixed_risk" and h_idx in acute_hours)

        # HR triplet
        hr_mean, hr_max, hr_min, hr_samples = gen_hr_triplet(
            state, rhr, max_hr, drift, af_state,
            is_spike=is_spike, is_acute=is_acute,
            prev_mean=prev_mean_hr,
        )
        prev_mean_hr = hr_mean

        # Steps (avg per minute)
        steps_avg = gen_steps(state)
        if is_spike: steps_avg = clamp(steps_avg, 0, 30)
        if is_acute: steps_avg = clamp(steps_avg, 0, 40)

        # HRV — sleep hours only; broadcast daily value later
        hrv_drift_pen = drift * 1.9
        hrv_raw = gen_hrv_rmssd(state, hrv_b, hrv_drift_pen, af_state, is_spike)
        raw_hrv_per_hour.append(hrv_raw)

        # Confidence
        conf = gen_confidence(cohort, h_idx in dropout_set, h_idx in partial_set)

        hourly.append({
            "timestamp":          ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "day":                day + 1,
            "hour":               hour,
            "activity_state":     state,
            "hr_mean":            round(hr_mean, 1),
            "hr_max":             hr_max,
            "hr_min":             hr_min,
            "hr_sample_count":    hr_samples,
            "avg_steps_per_min":  steps_avg,
            "af_status":          af_state,
            "sleep_flag":         state == "sleep",
            "is_strength_event":  state == "strength",
            "hrv_rmssd":          hrv_raw,   # None during waking hours
            "confidence":         conf,
            "true_acute_event":   gt_acute[h_idx],
            "true_chronic_event": gt_chronic[h_idx],
        })

    # ── Daily HRV — broadcast nightly value to all hours of that day ──────
    # This mirrors how the scoring engine will consume HRV (one per day,
    # derived from overnight readings)
    daily_hrv = build_daily_hrv(raw_hrv_per_hour, hrv_b)
    for h_idx, row in enumerate(hourly):
        row["hrv_rmssd_daily"] = daily_hrv[h_idx // HPD]
        # Keep raw hourly hrv_rmssd as-is (None for waking hours)

    return {
        "patient_id":        patient_id,
        "cohort":            cohort,
        "static":            static,
        "event_log":         event_log,
        "generator_version": "v3",
        "hourly":            hourly,
    }


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    patients = []; counters = {c: 1 for c in COHORTS}

    for cohort, n in COHORTS.items():
        print(f"Generating {n} {cohort} patients...")
        for i in range(n):
            pid = f"{PREFIX[cohort]}_{counters[cohort]:03d}"
            counters[cohort] += 1
            patients.append(generate_patient(pid, cohort))
            if (i + 1) % 10 == 0: print(f"  ...{i+1}/{n}")

    with open("patient_data_v3.json", "w") as f:
        json.dump({"patients": patients, "generator_version": "v3"}, f, indent=2)

    # ── Validation summary ────────────────────────────────────────────────
    print(f"\nDone. {len(patients)} patients · {sum(len(p['hourly']) for p in patients):,} records\n")
    print(f"{'Cohort':<20} {'n':>3}  {'af_hrs':>6}  {'true_ac':>7}  "
          f"{'true_ch':>7}  {'strength':>8}  {'dropout':>7}  {'hrv_null%':>9}")
    print("─" * 85)

    for cohort in COHORTS:
        pts = [p for p in patients if p["cohort"] == cohort]
        hrs = [h for p in pts for h in p["hourly"]]
        af_hrs   = sum(1 for h in hrs if h["af_status"] != "none")
        ta_hrs   = sum(1 for h in hrs if h["true_acute_event"])
        tc_hrs   = sum(1 for h in hrs if h["true_chronic_event"])
        st_hrs   = sum(1 for h in hrs if h["is_strength_event"])
        do_hrs   = sum(1 for h in hrs if h["confidence"]["score"] < 0.15)
        hrv_null = sum(1 for h in hrs if h["hrv_rmssd"] is None)
        hrv_pct  = hrv_null / len(hrs) * 100
        print(f"{cohort:<20} {len(pts):>3}  {af_hrs:>6}  {ta_hrs:>7}  "
              f"{tc_hrs:>7}  {st_hrs:>8}  {do_hrs:>7}  {hrv_pct:>8.1f}%")

    # Spot-check hr_max > hr_mean > hr_min
    violations = sum(
        1 for p in patients for h in p["hourly"]
        if not (h["hr_min"] <= h["hr_mean"] <= h["hr_max"])
    )
    print(f"\nhr_min ≤ hr_mean ≤ hr_max violations: {violations} (should be 0)")

    # Strength: hr_max in expected range
    strength_hrs = [h for p in patients for h in p["hourly"] if h["is_strength_event"]]
    if strength_hrs:
        avg_max = sum(h["hr_max"] for h in strength_hrs) / len(strength_hrs)
        avg_stp = sum(h["avg_steps_per_min"] for h in strength_hrs) / len(strength_hrs)
        print(f"Strength events: {len(strength_hrs)} hrs | "
              f"avg hr_max={avg_max:.0f} | avg steps/min={avg_stp:.1f}")

    import os
    print(f"\nOutput: patient_data_v3.json "
          f"({os.path.getsize('patient_data_v3.json')/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
