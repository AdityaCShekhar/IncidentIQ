# IncidentIQ strategy comparison

Generated: `2026-10-21T20:47:42.284413+00:00`

| Strategy | Version | Accuracy | Macro F1 | Coverage | Citation F1 | Failures | Mean latency (ms) | Prompt tokens | Completion tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | rules-v1 | 1.0000 | 0.8571 | 0.7500 | 0.7590 | 0 | 0.045 | n/a | n/a |
| llm_summary | summary-v2 | 0.2500 | 0.0000 | 0.0000 | 0.4412 | 4 | 20929.623 | 58947 | 15446 |
| rag | rag-v2 | 0.0625 | 0.0000 | 0.0000 | 0.0561 | 14 | 33630.324 | 97322 | 24167 |
| agent | agent-v2 | 0.0625 | 0.0000 | 0.0000 | 0.3910 | 9 | 37753.754 | 118506 | 22322 |

## Latency and benchmark-process resources

| Strategy | p50 (ms) | p95 (ms) | Process CPU (ms) | Lifetime peak RSS (bytes) |
| --- | ---: | ---: | ---: | ---: |
| rules | 0.046 | 0.095 | 0.728 | 42418176 |
| llm_summary | 16331.270 | 46705.206 | 225.555 | 47529984 |
| rag | 28718.566 | 73496.435 | 343.806 | 47529984 |
| agent | 33069.212 | 76360.96 | 494.877 | 47529984 |

CPU and lifetime peak RSS exclude the model server. RSS accumulates across strategies in the same process; it is not an isolated strategy memory comparison.

Per-strategy JSON, Markdown, and raw case artifacts are in sibling directories.
