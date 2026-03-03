# Validation Study Design V1

## Sample
- 100 simulated patients
- 30 days each
- Hourly data

## Patient Cohorts
**Healthy baseline**
- Number of samples: 30
- Stable vitals, random noise

**Anxiety phenotype**
- Number of samples: 20
- High sympathetic spikes, no pathology
  
**CHF phenotype**
- Number of samples: 20
- Gradual RHR drift + HRV decline

**AF-prone**
- Number of samples: 20
- Random AF events

**Mixed risk**
- Number of samples: 10
- Combined chronic + acute episodes

Each patient will need the following information:
- Static Attributes: Age, Diagnosis flags, Baseline RHR, Baseline HRV, Predicted max HR
- Hourly Variables: HR, Steps/min, AF detection, Sleep flag, HRV (daily resting calculation)
- Hourly confidence scores 

## Risk Engine
- Run acute & chronic risk engine to assign flag
- Evaluate flags for accuracy

## Evaluation Metrics
- Sensitivity = True acute flagged / Total acute events
- Specificity = True negatives / All non-acute
- False positive rate
- % anxiety spikes escalated incorrectly
- Detection rate of gradual deterioration
- Time-to-detection (days)
- False chronic flags in healthy cohort
- Flags per patient per month
- % red/critical
- % yellow
- % Insufficient data
- Mean daily alert volume
