# IncidentIQ benchmark report

- Dataset: `fixture-v1`
- Split: `dev`
- Strategy: `agent` (`agent-v2`)
- Generated: `2026-10-20T10:57:19.658931+00:00`
- Cases: 16

## Metrics

| Metric | Value |
| --- | ---: |
| Failed Cases | 16.0000 |
| Failure Rate | 1.0000 |
| Accuracy | 0.0000 |
| Macro F1 | 0.0000 |
| Coverage | 0.0000 |
| Abstention Rate | 0.0000 |
| Selective Accuracy | 0.0000 |
| Abstention Recall | 0.0000 |
| Citation Precision | 0.0000 |
| Citation Recall | 0.0000 |
| Citation F1 | 0.0000 |
| Mean Case Latency Ms | 61198.5920 |
| P50 Case Latency Ms | 60396.1100 |
| Max Case Latency Ms | 90045.5620 |

## Per-case results

| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |
| --- | --- | --- | --- | --- | --- | --- |
| iq-001 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-002 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-003 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-004 | slow_dependency | abstain | failed | yes | no | invalid_model_output |
| iq-007 | database_pool_exhaustion | abstain | failed | no | no | invalid_model_output |
| iq-008 | database_pool_exhaustion | abstain | failed | no | no | invalid_model_output |
| iq-009 | database_pool_exhaustion | abstain | failed | no | no | invalid_model_output |
| iq-010 | database_pool_exhaustion | abstain | failed | yes | no | invalid_model_output |
| iq-013 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-014 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-015 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-016 | deployment_regression | abstain | failed | yes | no | invalid_model_output |
| iq-019 | retry_storm | abstain | failed | no | no | model_timeout |
| iq-020 | retry_storm | abstain | failed | no | no | model_timeout |
| iq-021 | retry_storm | abstain | failed | no | no | model_timeout |
| iq-022 | retry_storm | abstain | failed | yes | no | invalid_model_output |

Authored synthetic fixtures; values are not measured production telemetry.
