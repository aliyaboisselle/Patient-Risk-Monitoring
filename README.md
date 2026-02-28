# Patient-Risk-Monitoring

*A theoretical pathway for ingesting consumer wearable data in a usable way.*

**This project is not meant to be used with real patient data. This project involves mock patient data and healthcare workflows, but are fictional and not clinically validated.**

## Purpose
Consumer wearables gather large amounts of data that, if synthesized correctly, could be used to proactively monitor for potential health events. With the correct calibration, this could be used to escalate critical events, getting patients the care they need at the moment they need it most. This architecture outlines an escalation system that ingests data and relevant EMR information to determine if and when to escalate irregular vitals.

## Project Status

This project is currently in the architecture and design phase.  
No production code or live integrations exist yet.

The goal of the upcoming V1 implementation is to validate:
- Wearable ingestion via mock API
- Risk scoring logic
- Flag generation workflow
- Basic dashboard auditability

This repository documents the proposed architecture and implementation roadmap.

## Architecture
**Wearable Input Layer**
Consumer wearables record several vitals that can provide insight into a user’s current health status. For the planned V1, the focus will be on vitals that are relatively accurate, heart rate (HR), resting heart rate (RHR), heart rate variability (HRV), sleep duration, step count, artrial fibrillation (AF). 

Consumer wearable data will be ingested via an API call. THe planned V1 simulates this with a mock API and consumer wearable data. 

**Wearable Risk Engine**
The wearable risk engine calculates the risk score for the combination of vitals that is provided by the consumer wearable. This engine will calculate a severity score based on the severity of vitals (0-100 scale). Once the severity is calculated, a confidence score is calculated. This is based on the duration of the vitals. For instance, if for a large part of the day the wearable did not gather data, confidence may be lower. Risk calculations should be displayed in the risk dashboard for auditability.

**EHR Modifier Layer (recommended, not required)**
The EHR Modifier layer is an optional layer that allows for an EHR API to be called to ingest specific relevant patient fields. For each diagnosis in scope, the EHR modifier should give a defined value (0-1.0) that will be used to adjust the vital risk score.

**Risk Calculation**
The outputs from the previous 2 sections, risk score and EHR modifier, are calculated with the following formula to determine a risk flag:

(risk 0-100)*(confidence 0-1.0)*(EHR modifier 0-1.0)=Risk

**Risk Flag**
Once a risk score has been calculated, a flag for risk can be set for the patient. For the planned V1, the following flag structure will be used:

| Flag | Behavior |
|------|----------|
| Green (0-50) | Vitals are good and/or confidence is low. No action needed, no follow up. |
| Yellow (>50-80) | Vitals are out of range, conditions may factor in, confidence is medium to high. Patient flagged with intelligent follow up questions if integration is available. Flag patient as potential risk on dashboard. |
| Red (>80-95) | Vitals are severely out of range, conditions may factor in, confidence high. Provider flagged to follow up with patient if integration available. Flag patient as risk on dashboard. |
| Critical (>95) | Vitals are severely out of range, conditions are present, confidence is high. If integration available, alert provider immediately. Patient needs medical care. Dashboard flags patient as critical risk on dashboard. |

**Risk Dashboard**
The risk flags will feed into a patient dashboard that displays the risk flag status and reasoning behind status. Statuses should allow auditability into the system decisions and a drilldown view with raw data and risk scoring calculations should be available.


## Tech Stack
| Layer | Technology |
|-------|------------|
| Wearable Input Integration | Integration with consumer wearable (eg. Open Wearables) |
| Wearable Risk Engine | Python Code Calculation |
| EHR Integration  | FHIR API Call |
| EHR Modifier | Python Code Calculation |
| Risk Flag | FHIR API - writes flag status to EHR or other system |
| EHR Configuration | Maps flag status to follow up action item (patient message, provider escalation) |
| Risk Dashboard | Auditing Dashboard - Patient, recent vitals, current flag, and reasoning displayed |

## Planned V1 Scope

- Mock wearable API ingestion
- Python-based risk engine
- Static rule-based severity model
- Configurable confidence scoring
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

## License
*MIT — free to use, adapt, and share.*
