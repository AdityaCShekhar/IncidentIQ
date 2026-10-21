# IncidentIQ benchmark report

- Dataset: `fixture-v1`
- Split: `dev`
- Strategy: `llm_summary` (`summary-v2`)
- Generated: `2026-10-21T20:27:37.761749+00:00`
- Cases: 16

## Metrics

| Metric | Value |
| --- | ---: |
| Failed Cases | 4.0000 |
| Failure Rate | 0.2500 |
| Accuracy | 0.2500 |
| Macro F1 | 0.0000 |
| Coverage | 0.0000 |
| Abstention Rate | 0.7500 |
| Selective Accuracy | 0.0000 |
| Abstention Recall | 1.0000 |
| Citation Precision | 0.9091 |
| Citation Recall | 0.2913 |
| Citation F1 | 0.4412 |
| Mean Case Latency Ms | 20929.6230 |
| P50 Case Latency Ms | 16331.2700 |
| Max Case Latency Ms | 46705.2060 |
| P95 Case Latency Ms | 46705.2060 |
| Process Cpu Time Ms | 225.5550 |
| Process Peak Rss Bytes | 47529984.0000 |
| Prompt Tokens | 58947.0000 |
| Completion Tokens | 15446.0000 |

## Per-case results

| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |
| --- | --- | --- | --- | --- | --- | --- |
| iq-001 | slow_dependency | abstain | failed | no | no | invalid_model_output |
| iq-002 | slow_dependency | abstain | abstained | no | no |  |
| iq-003 | slow_dependency | abstain | abstained | no | no |  |
| iq-004 | slow_dependency | abstain | abstained | yes | yes |  |
| iq-007 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-008 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-009 | database_pool_exhaustion | abstain | abstained | no | no |  |
| iq-010 | database_pool_exhaustion | abstain | abstained | yes | yes |  |
| iq-013 | deployment_regression | abstain | abstained | no | no |  |
| iq-014 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-015 | deployment_regression | abstain | failed | no | no | invalid_model_output |
| iq-016 | deployment_regression | abstain | abstained | yes | yes |  |
| iq-019 | retry_storm | abstain | abstained | no | no |  |
| iq-020 | retry_storm | abstain | abstained | no | no |  |
| iq-021 | retry_storm | abstain | failed | no | no | invalid_model_output |
| iq-022 | retry_storm | abstain | abstained | yes | yes |  |

Authored synthetic fixtures; values are not measured production telemetry.
CPU and lifetime peak RSS cover the benchmark process only; model-server resources are excluded. RSS is not an isolated per-case or per-strategy peak.
