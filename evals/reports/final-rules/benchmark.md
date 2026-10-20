# IncidentIQ benchmark report

- Dataset: `fixture-v1`
- Split: `held_out`
- Strategy: `rules` (`rules-v1`)
- Generated: `2026-10-20T12:41:40.808855+00:00`
- Cases: 8

## Metrics

| Metric | Value |
| --- | ---: |
| Failed Cases | 0.0000 |
| Failure Rate | 0.0000 |
| Accuracy | 1.0000 |
| Macro F1 | 1.0000 |
| Coverage | 1.0000 |
| Abstention Rate | 0.0000 |
| Selective Accuracy | 1.0000 |
| Abstention Recall | n/a |
| Citation Precision | 1.0000 |
| Citation Recall | 0.6562 |
| Citation F1 | 0.7925 |
| Mean Case Latency Ms | 0.1040 |
| P50 Case Latency Ms | 0.0920 |
| Max Case Latency Ms | 0.1770 |

## Per-case results

| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |
| --- | --- | --- | --- | --- | --- | --- |
| iq-005 | slow_dependency | slow_dependency | completed | no | yes |  |
| iq-006 | slow_dependency | slow_dependency | completed | no | yes |  |
| iq-011 | database_pool_exhaustion | database_pool_exhaustion | completed | no | yes |  |
| iq-012 | database_pool_exhaustion | database_pool_exhaustion | completed | no | yes |  |
| iq-017 | deployment_regression | deployment_regression | completed | no | yes |  |
| iq-018 | deployment_regression | deployment_regression | completed | no | yes |  |
| iq-023 | retry_storm | retry_storm | completed | no | yes |  |
| iq-024 | retry_storm | retry_storm | completed | no | yes |  |

Authored synthetic fixtures; values are not measured production telemetry.
