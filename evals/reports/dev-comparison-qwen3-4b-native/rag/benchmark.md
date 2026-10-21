# IncidentIQ benchmark report

- Dataset: `fixture-v1`
- Split: `dev`
- Strategy: `rag` (`rag-v2`)
- Generated: `2026-10-21T20:37:38.212861+00:00`
- Cases: 16

## Metrics

| Metric | Value |
| --- | ---: |
| Failed Cases | 14.0000 |
| Failure Rate | 0.8750 |
| Accuracy | 0.0625 |
| Macro F1 | 0.0000 |
| Coverage | 0.0000 |
| Abstention Rate | 0.1250 |
| Selective Accuracy | 0.0000 |
| Abstention Recall | 0.2500 |
| Citation Precision | 0.7500 |
| Citation Recall | 0.0291 |
| Citation F1 | 0.0561 |
| Mean Case Latency Ms | 33630.3240 |
| P50 Case Latency Ms | 28718.5660 |
| Max Case Latency Ms | 73496.4350 |
| P95 Case Latency Ms | 73496.4350 |
| Process Cpu Time Ms | 343.8060 |
| Process Peak Rss Bytes | 47529984.0000 |
| Prompt Tokens | 97322.0000 |
| Completion Tokens | 24167.0000 |

## Per-case results

| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |
| --- | --- | --- | --- | --- | --- | --- |
| iq-001 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-002 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-003 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-004 | slow_dependency | abstain | failed | yes | no | invalid_model_output |
| iq-007 | database_pool_exhaustion | abstain | failed | no | no | invalid_model_output |
| iq-008 | database_pool_exhaustion | abstain | failed | no | no | invalid_model_output |
| iq-009 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-010 | database_pool_exhaustion | abstain | failed | yes | no | invalid_model_output |
| iq-013 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-014 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-015 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-016 | deployment_regression | abstain | abstained | yes | yes |  |
| iq-019 | retry_storm | abstain | failed | no | no | invalid_model_output |
| iq-020 | retry_storm | abstain | failed | no | no | invalid_model_output |
| iq-021 | retry_storm | abstain | failed | no | no | invalid_model_output |
| iq-022 | retry_storm | abstain | failed | yes | no | invalid_model_output |

Authored synthetic fixtures; values are not measured production telemetry.
CPU and lifetime peak RSS cover the benchmark process only; model-server resources are excluded. RSS is not an isolated per-case or per-strategy peak.
