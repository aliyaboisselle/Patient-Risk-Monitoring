# Patient-Risk-Monitoring

*A theoretical pathway for ingesting consumer wearable data in a usable way.*

**This project is not meant to be used with real patient data. This project involves mock patient data and healthcare workflows, but are fictional and not clinically validated.**

## Purpose
The synthetic mock study v1 provides a method for displaying risk calculations and escalations defined in the architecture. This is a mock study. This project is not meant for clinical use and has not been clinically validated. DO NOT PUT PHI INTO THIS SCORING ENGINE. PHI SECURITY HANDLING IS NOT IN SCOPE.

## Project Status

This project is currently in the architecture and mock study phase.
No production code or live integrations exist yet.

The goal of this V1 implementation is to validate:
- Wearable ingestion via mock API
- Risk scoring logic
- Flag generation workflow
- Basic dashboard auditability

This repository documents the proposed architecture and implementation roadmap.

## How to Run
Below are the instructions on how to run the synthetic study. 
1. Run the generate patient data v1.py
   - This will give you a JSON file with synthetic patient data compatible with either the dashboard directly or with the scoring engine. The default is Seed 42, but other seeds can be used. You can also rename the output file. 
  -  Sample run command: python3 generate_patient_data_v1.py --seed 42 --output patient_data_v1.json
2. If you prefer to run the scoring py and generate a JSON, you can then run the scoring py using your generated mock data
  - Sample run:  python3 scoring_engine_v1.py --data patient_data_v1.json --hour 360 --patient CF_001
3. Either input the file from #1 or #2 into the dashboard (output from #1 would go in data field, output from #2 would go into results field - only one is needed). This will allow you to fiew and audit the results of the scoring engine. Metrics tab will give an overview of how the scoring engine is performing based on results audit.

## Deviations From Architecture
- DEV-001 [af_status field, §3]: Spec defines af_minutes (numeric). Implementation
    uses af_status (categorical string) to enable direct severity table lookup.

- DEV-002 [activity_state extensions, §4.1]: Spec's activity modifier table does
    not include "strength", "cardio", or "light" activity states. Implementation
    adds these as priority-0 rules evaluated before the step-count rules:
      strength → ×0.75  (high HR + low steps expected; wearable gym classification)
      cardio   → ×0.50  (stationary cardio; without this, fires high_hr_sedentary ×1.5)
      light    → ×0.75  (light activity; HR elevation expected, not concerning)
    Without cardio handling, a healthy patient on a stationary bike fires RED.
    Approved extension.

- DEV-003 [24-hour cooldown + active case suppression, §8]: Spec requires a 24-hour
    cooldown for same/lower flags and suppression when an active case exists. These
    require persistent state across scoring runs and are NOT implemented in v1.
    Required before any production deployment.

- DEV-004 [active_minutes field, §3]: Spec defines active_minutes per hourly pull.
    Implementation uses avg_steps_per_min as a proxy. The duration >10 min gate on
    Score-100 conditions cannot be fully evaluated without this field.


## Architecture
**Wearable Input Layer**
Consumer wearables record several vitals that can provide insight into a user’s current health status. For the planned V1, the focus will be on vitals that are relatively accurate, heart rate (HR), resting heart rate (RHR), heart rate variability (HRV), sleep duration, step count, artrial fibrillation (AF). 

Consumer wearable data will be ingested via an API call. THe planned V1 simulates this with a mock API and consumer wearable data. 

**Wearable Risk Engines**
The wearable risk engines calculate the risk score for the combination of vitals that is provided by the consumer wearable for both chronic decompensation and acute danger. These engines will calculate a severity score based on the severity of vitals (0-100 scale). Once the severity is calculated, a persistence modifier is added. This is based on the duration of the vitals. Risk calculations should be displayed in the risk dashboard for auditability.

**EHR Modifier Layer (recommended, not required)**
The EHR Modifier layer is an optional layer that allows for an EHR API to be called to ingest specific relevant patient fields. For each diagnosis in scope, the EHR modifier should give a defined value (0-1.0) that will be used to adjust the vital risk score.

**Risk Calculation**
The outputs from the previous 2 sections, risk score and EHR modifier, and a confidence modifier that determines the reliability of the data are calculated with the following formula to determine a risk flag:

(risk 0-100)*(confidence 0-1.0)*(EHR modifier 0-1.0)=Risk

**Risk Flag**
Once a risk score has been calculated, a flag for risk can be set for the patient. For the planned V1, the following flag structure will be used:

| Flag | Behavior |
|------|----------|
| Green | Vitals are good and/or confidence is low. No action needed, no follow up. |
| Yellow | Vitals are out of range, conditions may factor in, confidence is medium to high. Patient flagged with intelligent follow up questions if integration is available. Flag patient as potential risk on dashboard. |
| Red | Vitals are severely out of range, conditions may factor in, confidence high. Nurse flagged to follow up with patient if integration available. Flag patient as risk on dashboard. |
| Critical | Vitals are severely out of range, conditions are present, confidence is high. If integration available, alert provider immediately. Patient needs medical care. Dashboard flags patient as critical risk on dashboard. |

**Risk Dashboard**
The risk flags will feed into a patient dashboard that displays the risk flag status and reasoning behind status. Statuses should allow auditability into the system decisions and a drilldown view with raw data and risk scoring calculations should be available.


## Tech Stack
| Layer | Technology |
|-------|------------|
| Wearable Input Integration | Integration with consumer wearable (eg. Open Wearables) |
| Wearable Risk Engines | Python Code Calculation |
| EHR Integration  | FHIR API Call |
| EHR Modifier | Python Code Calculation |
| Risk Flag | FHIR API - writes flag status to EHR or other system |
| EHR Configuration | Maps flag status to follow up action item (patient message, provider escalation) |
| Risk Dashboard | Auditing Dashboard - Patient, recent vitals, current flag, and reasoning displayed |

## Planned V1 Synthetic Study Scope

- Mock wearable API ingestion
- Python-based risk engine
- Static rule-based severity model
- Confidence scoring
- Risk flag assignment
- Mock EHR ingestion and write
- Simple audit dashboard (local)

Out of scope for V1:
- Clinical validation
- Live EHR integration
- Real patient data
- Production deployment
  
## Open Problems
| Problem | Status |
|-------|------------|
| Diagnosis escalation modifier requires clinical input | Currently, modifiers are not clinically validated or tuned. This framework would require clinical input and validation. Additionally each added diagnosis requires clinical input and validation|
| Potential for Alert Fatigue | Alert fatigue is a prominent problem amongst clinical workflows. Any additional alert system must be tuned to both limit escalation to providers while also providing meaningful escalations when appropriate. The patient question route attempts to address this, however, further tuning will be needed. |
| Data ingestion timing | Consumer wearables record data consistently. Determining the right amount of time between pulls is an open question. This is a configurable option in the architecture, but additional research is needed to understand where the recommendation should be. |
| Consumer wearable reliability and human activity | Consumer wearable reliability is not always consistent and human activity is often variable. Additional research is needed to tune for both. |

## Phase 2+ Improvements

- Additional diagnosis support
- Additional vital information
- Advanced risk calculation formulas, including additional demographic, activity, and baseline data

## License
*MIT — free to use, adapt, and share.*
