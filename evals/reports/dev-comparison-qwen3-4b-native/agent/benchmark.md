# IncidentIQ benchmark report

- Dataset: `fixture-v1`
- Split: `dev`
- Strategy: `agent` (`agent-v2`)
- Generated: `2026-10-21T20:47:42.277030+00:00`
- Cases: 16

## Metrics

| Metric | Value |
| --- | ---: |
| Failed Cases | 9.0000 |
| Failure Rate | 0.5625 |
| Accuracy | 0.0625 |
| Macro F1 | 0.0000 |
| Coverage | 0.0000 |
| Abstention Rate | 0.4375 |
| Selective Accuracy | 0.0000 |
| Abstention Recall | 0.2500 |
| Citation Precision | 0.8667 |
| Citation Recall | 0.2524 |
| Citation F1 | 0.3910 |
| Mean Case Latency Ms | 37753.7540 |
| P50 Case Latency Ms | 33069.2120 |
| Max Case Latency Ms | 76360.9600 |
| P95 Case Latency Ms | 76360.9600 |
| Process Cpu Time Ms | 494.8770 |
| Process Peak Rss Bytes | 47529984.0000 |
| Prompt Tokens | 118506.0000 |
| Completion Tokens | 22322.0000 |

## Per-case results

| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |
| --- | --- | --- | --- | --- | --- | --- |
| iq-001 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-002 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-003 | slow_dependency | abstain | abstained | no | no |  |
| iq-004 | slow_dependency | abstain | failed | yes | no | invalid_model_output |
| iq-007 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-008 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-009 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-010 | database_pool_exhaustion | abstain | failed | yes | no | invalid_model_output |
| iq-013 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-014 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-015 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-016 | deployment_regression | abstain | failed | yes | no | invalid_model_output |
| iq-019 | retry_storm | abstain | abstained | no | no |  |
| iq-020 | retry_storm | abstain | abstained | no | no |  |
| iq-021 | retry_storm | abstain | failed | no | no | invalid_model_output |
| iq-022 | retry_storm | abstain | abstained | yes | yes |  |

Authored synthetic fixtures; values are not measured production telemetry.
CPU and lifetime peak RSS cover the benchmark process only; model-server resources are excluded. RSS is not an isolated per-case or per-strategy peak.
