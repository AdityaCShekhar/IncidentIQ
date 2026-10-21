# W6B comparative evaluation

Completed: 2026-10-22. Outcome: no measured model-backed improvement over rules in the tested configuration. This closes the comparison with a documented negative result, as allowed by the specification; it does not establish AI diagnosis quality or meet every aspirational metric target.

## Native Ollama follow-up: 2026-10-22

The [native 4B comparison](../evals/reports/dev-comparison-qwen3-4b-native/comparison.md) ran all four strategies on the same 16 frozen development cases. Native Ollama 0.40.7 used `qwen3:4b-instruct`, Q4_K_M, on the Apple M4 Pro with 24 GB unified memory. Ollama reported 100% GPU use and 3,877,571,133 bytes of loaded-model/GPU memory with an 8,192-token context; this is a point observation, not peak total application memory. The model digest and observation are in the [preflight artifact](../evals/reports/native-ollama-preflight/loaded-model.json).

| Strategy | Correct | Valid reports | Validation failures | Diagnosis coverage | Median ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | 16/16 | 16/16 | 0 | 0.75 | 0.046 | 0.095 |
| llm_summary | 4/16 | 12/16 | 4 | 0.00 | 16331.270 | 46705.206 |
| rag | 1/16 | 2/16 | 14 | 0.00 | 28718.566 | 73496.435 |
| agent | 1/16 | 7/16 | 9 | 0.00 | 33069.212 | 76360.960 |

All accepted model-backed reports abstained. Correct decisions therefore came only from expected-abstention cases: four for summary, one for RAG, and one for agent. No accepted root-cause diagnosis was produced. All 27 failed model-backed cases were `invalid_model_output`; none timed out. Native inference and valid-report generation are verified, while diagnostic usefulness is poor on this authored development set. The agent did not improve on rules or summary accuracy.

The initial 384-token preflight on `iq-001` stopped at `done_reason=length` twice and produced incomplete JSON. At 1,536 tokens, it produced complete JSON but failed supporting-evidence validation. Both attempts are preserved in [preflight results](../evals/reports/native-ollama-preflight/). Inspection of the full run also found unsupported hypotheses, conflicting citation lists, and nonexistent evidence IDs. The existing schema, citation validators, tool allowlist, and 90-second agent deadline were retained.

The native run explicitly used a 1,536-token completion budget and a 120-second adapter timeout. The adapter default remains 384 tokens for reproducibility. Model, runtime, generation budget, and timeout differ from the historical 0.5B run, so changes cannot be attributed to model size alone. These results use exact cause/alias scoring on unaudited synthetic labels, not an independent semantic audit or a production incident evaluation. Held-out cases were not executed.

The adapter now records stop reasons and retains the final rejected diagnosis text plus completed-call token usage in benchmark artifacts. Agent diagnosis failures also retain planner usage; transport failures can still have unknown token counts. The native run has complete recorded token totals. See the [setup and reproduction guide](native-ollama.md).

Final verification: 160 offline tests passed; frozen dataset validation and changed-file Ruff lint/format passed. PostgreSQL integration was not rerun. Repository-wide lint still has pre-existing violations outside the changed files.

## Historical same-case strategy comparison

The [2026-10-20 comparison](../evals/reports/dev-comparison-qwen05-bounded/comparison.md) ran S0–S3 on the same 16 frozen development fixtures with `qwen2.5:0.5b` for S1–S3. These are authored synthetic observations with unaudited labels. Accuracy includes appropriate abstention; failures count as incorrect. The median and nearest-rank p95 below come directly from each saved `benchmark.json`'s per-case wall times. With 16 observations, nearest-rank p95 is the maximum; these are descriptive measurements, not stable tail estimates.

| Strategy | Correct | Coverage | Citation F1 | Failures | Median ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S0 rules | 16/16 | 0.75 | 0.7590 | 0 | 0.100 | 0.375 |
| S1 LLM summary | 0/16 | 0 | 0 | 16 | 50118.678 | 85988.702 |
| S2 RAG | 0/16 | 0 | 0 | 16 | 35400.568 | 54764.601 |
| S3 agent | 0/16 | 0 | 0 | 16 | 60396.110 | 90045.562 |

Rules provided 12 supported diagnoses and four expected abstentions. Model-backed strategies returned no valid diagnoses, so their zero scores describe end-to-end failure, not the semantic correctness of accepted reports. Retrieval and extra tools produced no measurable diagnosis benefit in this run. A larger model might behave differently; no such claim is supported by these artifacts. S3 incurred higher median latency than S1 or S2 without improving successful coverage.

The separate [rules held-out report](../evals/reports/final-rules/benchmark.md) scored 8/8. Those eight cases contain no expected abstentions. Model strategies were not scored on the held-out split, and this session did not rerun it or tune against it.

## Failure accounting and examples

| Strategy | Invalid model output | Model timeout |
| --- | ---: | ---: |
| S1 | 16 | 0 |
| S2 | 16 | 0 |
| S3 | 13 | 3 |

- S1 on `iq-001` failed with `invalid_model_output` after 39373.696 ms; see its [saved case results](../evals/reports/dev-comparison-qwen05-bounded/llm_summary/benchmark.json).
- S2 on `iq-001` failed with `invalid_model_output` after 34483.819 ms; see its [saved case results](../evals/reports/dev-comparison-qwen05-bounded/rag/benchmark.json).
- S3 on `iq-019` hit `model_timeout` after 90045.562 ms. Cases `iq-020` and `iq-021` also timed out; see [agent case results](../evals/reports/dev-comparison-qwen05-bounded/agent/benchmark.json).

The historical failure records have null raw model output and token metadata. They establish the recorded failure category, but cannot support inspection of the rejected text, token-cost totals, or a specific explanation such as completion truncation. Schema-constrained generation and one repair did not yield valid outputs in this configuration. No failure text has been reconstructed or invented.

## Resource observations and verification

The [2026-10-22 rules verification](../evals/reports/w6b-rules-verification/benchmark.md) reran the same development cases with locked dependencies on Python 3.12.14, Darwin arm64. It recorded 16/16 correct, median 0.040 ms, nearest-rank p95 0.109 ms, 0.667 ms total process CPU during case evaluation, and a 43,515,904-byte process lifetime peak RSS. This is a separate run from the four-strategy comparison and is not a cross-machine performance comparison.

The harness now saves per-case process CPU time and lifetime peak RSS, aggregate p95 latency and failure counts, and platform/resource-scope metadata. RSS is the benchmark process's cumulative high-water mark, including imports and prior cases. In a shared comparison process it also includes earlier strategies. It excludes a separate Ollama server, GPU memory, and child processes, so it must not be presented as inference memory consumption or isolated strategy memory. System resource costs of the historical model run were not captured and cannot be recovered. Unknown token usage remains null, including when only some cases have token metadata.

The frozen dataset checksum validator passed. Offline scoring/accounting tests cover correct abstention, failure scoring, missing token counts, report serialization, resource scope, and the held-out opt-in guard. Simulator verification also restored the existing HTTP 502 contract for invalid downstream status responses while preserving HTTP 503 for explicitly unavailable inventory. See the tracker for the final suite result. Repository-wide lint has pre-existing violations outside these changed files; targeted lint and formatting pass.

## Remaining evaluation limits

The native 4B follow-up above supersedes the previous runtime limitation. Neither tested model configuration established a diagnostic improvement over rules. Further model and prompt evaluations remain optional follow-up work. Independent blind label review, healthy and held-out incomplete cases, semantic unsupported-claim auditing, stage-level latency breakdowns, and peak inference-server resource measurements remain follow-up work. The frozen v1 dataset and historical reports are preserved. No optional W7 work is included.
