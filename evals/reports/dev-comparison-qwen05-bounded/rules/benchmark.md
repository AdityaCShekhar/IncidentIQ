# IncidentIQ benchmark report

- Dataset: `fixture-v1`
- Split: `dev`
- Strategy: `rules` (`rules-v1`)
- Generated: `2026-10-20T10:17:17.913638+00:00`
- Cases: 16

## Metrics

| Metric | Value |
| --- | ---: |
| Failed Cases | 0.0000 |
| Failure Rate | 0.0000 |
| Accuracy | 1.0000 |
| Macro F1 | 0.8571 |
| Coverage | 0.7500 |
| Abstention Rate | 0.2500 |
| Selective Accuracy | 1.0000 |
| Abstention Recall | 1.0000 |
| Citation Precision | 1.0000 |
| Citation Recall | 0.6117 |
| Citation F1 | 0.7590 |
| Mean Case Latency Ms | 0.1360 |
| P50 Case Latency Ms | 0.1000 |
| Max Case Latency Ms | 0.3750 |

## Per-case results

| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |
| --- | --- | --- | --- | --- | --- | --- |
| iq-001 | slow_dependency | slow_dependency | completed | no | yes |  |
| iq-002 | slow_dependency | slow_dependency | completed | no | yes |  |
| iq-003 | slow_dependency | slow_dependency | completed | no | yes |  |
| iq-004 | slow_dependency | abstain | abstained | yes | yes |  |
| iq-007 | database_pool_exhaustion | database_pool_exhaustion | completed | no | yes |  |
| iq-008 | database_pool_exhaustion | database_pool_exhaustion | completed | no | yes |  |
| iq-009 | database_pool_exhaustion | database_pool_exhaustion | completed | no | yes |  |
| iq-010 | database_pool_exhaustion | abstain | abstained | yes | yes |  |
| iq-013 | deployment_regression | deployment_regression | completed | no | yes |  |
| iq-014 | deployment_regression | deployment_regression | completed | no | yes |  |
| iq-015 | deployment_regression | deployment_regression | completed | no | yes |  |
| iq-016 | deployment_regression | abstain | abstained | yes | yes |  |
| iq-019 | retry_storm | retry_storm | completed | no | yes |  |
| iq-020 | retry_storm | retry_storm | completed | no | yes |  |
| iq-021 | retry_storm | retry_storm | completed | no | yes |  |
| iq-022 | retry_storm | abstain | abstained | yes | yes |  |

Authored synthetic fixtures; values are not measured production telemetry.
