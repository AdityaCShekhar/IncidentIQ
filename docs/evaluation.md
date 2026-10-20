# Evaluation dataset and deterministic baseline

The project includes four comparable strategies: deterministic rules (S0), Ollama-backed summary (S1), retrieval-augmented diagnosis (S2), and bounded read-only tool investigation (S3). Their code paths and benchmark reports exist, but the tested low-resource model did not produce a valid model-backed diagnosis; AI diagnostic quality therefore remains unverified. The rules results are measured on authored synthetic fixtures only.

## Dataset v1

| Property | Contents |
| --- | --- |
| Version | `fixture-v1` |
| Provenance | `authored_synthetic_fixture`; synthetic numbers, not measured telemetry |
| Cases | 24; six per fault family |
| Fault families | Slow dependency, database-pool exhaustion, deployment regression, retry storm |
| Development split | 16 cases, including four incomplete cases that should abstain |
| Held-out split | Eight cases; a final rules-v1 report has been generated, while model strategies remain unscored on this split |
| Files | `evals/cases.jsonl`, `evals/expected_evidence.jsonl`, `evals/fixtures/*.json` |
| Freeze | SHA-256 checksums in `evals/manifest.json` |

Each family has clear, early/shorter, unrelated-noise, missing-evidence, instruction-text, and service-name variations. Instruction-text and service-name variations are held out. Every case specifies a seed, causal configuration, incident window, primary cause and aliases, affected services and dependency chain, alternatives, a safe diagnostic step, severity, and expected abstention behavior. Expectations separately identify relevant and irrelevant evidence UUIDs, observation roles, and evidence predicates.

Ground-truth labels come from the declared fixture mechanisms in [the authoring recipe](../scripts/author_eval_fixtures.py), independently of diagnosis outputs. The recipe does not import or invoke a diagnosis strategy or model. It constructs observations, passes them through the existing evidence normalizer, and writes labels separately. These are repository-authored contracts, not labels derived from an LLM judge, and have not received an independent human audit. A [blind label-review protocol](evaluation-label-review.md) describes how to conduct that audit without exposing the answer key first.

Pool-exhaustion and retry-storm faults have bounded HTTP simulator commands and authored fixtures. All four simulator faults and Docker telemetry collection were exercised; live pool-exhaustion and retry-storm artifacts are in [the W6A report directory](../evals/reports/w6a/). The authored labels have not received an independent human audit, and the cases are not measured production captures.

### Validate frozen data

After installing development dependencies with `make sync`:

```sh
make dataset
```

This validates all 24 cases without executing a diagnosis strategy. Checks include file checksums, case/split consistency, evidence scope and timestamps, normalized content hashes and UUIDs, unique references, and classification of every observation in the expected-evidence file. Input reads are bounded, and manifest paths must remain within the dataset directory.

Normal development must preserve these files. If labels or inputs need correction, retain the old version, document the reason, and create a new dataset version with new checksums. Checksums detect accidental edits; they do not authenticate data or prevent someone from deliberately changing both data and the manifest.

The authoring recipe requires an explicit output directory and refuses to overwrite a nonempty directory. To reproduce the current bytes elsewhere:

```sh
uv run python scripts/author_eval_fixtures.py --output-root /tmp/incidentiq-fixture-copy
uv run python scripts/validate_dataset.py --directory /tmp/incidentiq-fixture-copy
```

Choose a new empty path if that directory already exists. The recipe currently authors version `fixture-v1`; creating a different dataset version requires an explicit code and documentation change. Its repeatability is covered by a byte-for-byte check.

## S0 rules strategy

The baseline accepts an `EvidenceBundle` through `run_rules(bundle)`. It receives no case label, scenario configuration, expected evidence, or split metadata. It reads structured fields and correlated spans. Free-form logs and summaries do not select causes or trigger operations. No model, database, network, shell, or diagnostic tools are called by the strategy.

| Pattern | Required observations |
| --- | --- |
| Slow dependency | At least two distinct traces; upstream duration at least 200 ms; exactly one contained dependency client call taking at least 150 ms and 70% of upstream duration, returning HTTP 200 |
| Pool exhaustion | Same-service, same-pool gauges: at least five waiting acquisitions and utilization from 0.9 to 1.0, within ten seconds of one another and a slow/erroring service request |
| Deployment regression | A succeeded deployment event followed by at least two failing requests with matching revision attributes and successful contained dependency calls |
| Retry storm | At least two slow requests, each with three to six contained calls on one dependency operation, sequential explicit retry indices starting at zero, and at least two failed attempts |

Spans are joined by trace ID and parent span ID, and client intervals must fit inside their parent. Rules scope metrics and deployment events to the affected service. Cumulative metric snapshots do not establish pool saturation; only the named gauges are accepted. Pool/retry diagnostic attributes use project-owned names: `incidentiq.pool.name` and `incidentiq.retry_attempt`. The retry simulator emits explicit zero-based retry indices. Docker evidence confirmed these attributes and behaviors in the W6A pool-exhaustion and retry-storm runs.

Matching causes receive a fixed heuristic confidence of 0.7. This is not a measured or calibrated probability. Multiple matches return `inconclusive` with up to three hypotheses; equal scores sort by cause name for deterministic output. No match returns `insufficient_evidence` and an `unknown` hypothesis with confidence zero. Partial collection is disclosed even when a pattern matches. The baseline does not prove causality from a deployment's timing, infer missing retry attributes, or resolve complex competing causes.

Each returned cause has supporting UUIDs. The shared Pydantic report schema and deterministic validator reject unknown IDs, mixed-incident evidence, missing support for a material cause, and duplicate/conflicting citation lists. ID resolution proves that a record exists; it does not prove that its content supports a claim. Contradiction detection is not implemented by S0, so its contradicting-evidence lists are empty. The expected-evidence annotations identify relevance and observation roles, not an exhaustive semantic claim-support judgement.

### Diagnose one bundle

No running services or model runtime are needed for the authored example:

```sh
make diagnose EVIDENCE=evals/fixtures/iq-001.json
```

For actual simulator evidence, first run the scenario and collect its exported fault window, then:

```sh
make diagnose EVIDENCE=docs/demos/slow-dependency-evidence.json
```

To save a result atomically:

```sh
uv run python scripts/diagnose.py \
  --evidence docs/demos/deployment-regression-evidence.json \
  --output /tmp/incidentiq-rules-report.json
```

Output records strategy/version, incident UUID, `model: null`, and the typed report. Exit zero means the computation succeeded, including an abstention; inspect `report.disposition`. Invalid evidence, references, scope, or file operations fail with a safe error and nonzero status. The CLI rejects an output path that would overwrite its input. For persisted investigations, use `POST /incidents/{id}/investigations` with `{"strategy":"rules"}`; see the [API walkthrough](api.md#run-an-investigation).

## Run the benchmark

Run the development split with the S0 rules baseline:

```sh
make benchmark
```

This writes `benchmark.json`, `benchmark.md`, and per-case `raw-results.jsonl` under
`evals/reports/dev-rules/`. Choose another output directory with
`make benchmark BENCHMARK_ARGS='--output-dir /tmp/incidentiq-benchmark'`. The command validates
the frozen dataset first and passes only each evidence bundle to the strategy; labels are read
after a report is returned. Reports retain dataset and strategy versions, the split, generation
time, every raw typed report, and aggregate metrics.

Run all four strategies on the same development cases with a local Ollama model:

```sh
make benchmark BENCHMARK_ARGS='--strategy all --model MODEL_TAG'
```

This runs rules, LLM summary, RAG, and agent strategies; writes each strategy's JSON, Markdown,
and raw case results to `evals/reports/dev-comparison/{strategy}/`; and writes
`comparison.json`/`comparison.md` at the comparison root. Individual case failures are recorded and
counted instead of aborting the comparison. Reports include per-case wall latency, aggregate
latency percentiles, available Ollama token counts, failure rates, accuracy, abstention, and
evidence citation metrics. The comparison is an offline authored-fixture result, not production
telemetry or a claim that an agent strategy is better.

The headline accuracy is exact top-hypothesis cause accuracy, counting a correct abstention on
an expected-incomplete case as correct. `macro_f1` averages one-vs-rest F1 across the four gold
causes; abstentions count as missed predictions. `coverage` is the fraction with a non-abstaining
diagnosis, `selective_accuracy` is accuracy among those diagnoses, and `abstention_recall` is the
fraction of expected-incomplete cases that abstain. Citation precision/recall/F1 compare the top
hypothesis support IDs with relevant evidence IDs, pooled across cases; this measures annotation
overlap, not semantic claim support. The confusion matrix includes an `abstain` prediction column.

The held-out split is reserved and the runner rejects it unless explicitly enabled:

```sh
uv run python scripts/run_benchmark.py --split held_out --final-report \
  --output-dir evals/reports/final-rules
```

Use this only for a final report after strategy choices are fixed. Any inspection followed by
tuning makes those cases development data; create a new holdout before claiming a final result.

### S1 local LLM summary

The `llm_summary` strategy sends a bounded evidence-only prompt to a local Ollama instance. It
does not retrieve knowledge or call diagnostic tools. Install and start Ollama separately, pull a
model supported by your machine, then run:

```sh
make benchmark BENCHMARK_ARGS='--strategy llm_summary --model MODEL_TAG'
```

Replace `MODEL_TAG` with the installed model tag. The default endpoint is loopback
`http://127.0.0.1:11434`; remote model endpoints are rejected. The adapter uses Ollama's native
chat API with a JSON Schema response format, a 30-second timeout, temperature 0, seed 17, and
context size 8192. The timeout can be raised to 120 seconds with `--timeout`; model output is
capped at 384 tokens. It includes at most 100 observations and a 48,000-character prompt budget.
The `summary-v2` prompt asks for concise output. Evidence text is explicitly treated as untrusted
data. Invalid output or citations get one repair call; a second invalid response fails the case.
Valid references must belong to observations actually included in the compact prompt. RAG and
agent workflows have a 90-second total deadline, including their retrieval or planning work.

Per-case benchmark outputs retain the raw model response, reported model tag, generation settings,
prompt template version, token counts when provided by Ollama, and measured call latency. These
are local model measurements; there is no hosted-model cost field. The official API documents the
`/api/chat` endpoint's JSON Schema `format` and usage metadata in the [Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md).

## Checks and limitations

The dataset and rules checks cover fixture integrity, causal contracts, ordering, abstention, scope, and citations. The benchmark emits aggregate metrics, metadata, raw outputs, and Markdown/JSON summaries. Some rules checks use real simulator SDK output rather than authored fixtures. The historical offline suite had 148 passing tests before the benchmark harness and later workflow additions; see [TASKS.md](../TASKS.md) for the recorded scope and runtime verification status. Those historical results do not validate every later change.

The held-out fixtures are parsed for integrity but are not diagnosed by tests. A separate final benchmark report scored frozen rules-v1 on all eight held-out cases; this does not change test coverage. The benchmark command defaults to `dev` and requires an explicit final-report choice for `held_out`. Do not tune rules or prompts after inspecting held-out outcomes without treating that set as development data and creating another holdout.

This small dataset is deliberately simple and balanced by fault family. It shares authored templates, has only one cause per case, has no healthy/no-incident cases, and its held-out split currently has no incomplete cases. Development checks do not demonstrate generalization, calibrated confidence, or production usefulness. A later dataset should add independently reviewed live captures, healthy controls, stronger confounders, and held-out incomplete cases under a new version. Cross-strategy reports must retain this version and disclose these gaps.

The 2026-10-20 development comparison is saved in
[`evals/reports/dev-comparison-qwen05-bounded/`](../evals/reports/dev-comparison-qwen05-bounded/).
The rules baseline completed all 16 cases with 1.00 accuracy, 0.857 macro-F1, and 0.759 citation
F1. With `qwen2.5:0.5b`, all 16 `llm_summary` and RAG cases failed output validation; all 16 agent
cases failed (13 invalid outputs and 3 timeouts). Those model-strategy accuracy values are not
diagnostic quality measurements because no structured diagnosis passed validation. The report
records mean case latencies of 0.136 ms (rules), 50.98 s (summary), 37.93 s (RAG), and 61.20 s
(agent). Prompt/completion token counts were unavailable for the failed calls. This is a measured
model compatibility failure on the authored dev fixtures, not evidence of production accuracy or
an improvement over rules.

The separate [final rules-v1 report](../evals/reports/final-rules/benchmark.md) scored all eight
held-out cases correctly (accuracy and macro-F1 1.00, zero failures). Citation precision was 1.00,
recall 0.6563, and F1 0.7925. Mean per-case latency was 0.104 ms on this run. These eight authored
fixtures contain no expected-abstention cases, and their labels have not received independent
human review; this result does not establish generalization to real incidents.

The simulator exposes four bounded repeatable fault commands: `slow_dependency`,
`deployment_regression`, `pool_exhaustion`, and `retry_storm`. For investigation safety, evidence and
retrieved text are marked untrusted, tool names are selected from a fixed read-only registry, and
tool inputs are Pydantic-validated. The agent has a five-tool-call ceiling and a three-model-call
ceiling. Docker verification exercised rejection of a disallowed tool plan, invalid tool arguments,
retrieval failure, model timeout, malformed output, unavailable telemetry, and persistence of failed
runs and audits; see [W6A artifacts](../evals/reports/w6a/). Resistance to prompt injection and the
quality of valid model diagnoses remain unverified. The 2026-10-20 comparison reports latency and
token counts when available, but did not measure system resource consumption. Higher-resource
model evaluation is deferred on the current low-end system.

## Knowledge retrieval

The curated corpus in `knowledge/` contains service runbooks and four prior incident summaries.
`scripts/evaluate_retrieval.py` splits documents on Markdown headings and assigns each section a
stable UUID from its corpus version, source, heading, and text. Chunks retain document, section,
source, type, service, and cause tags. It compares BM25-style lexical ranking, cosine ranking over
local hashed word and character-ngram feature vectors, and reciprocal-rank fusion (RRF, constant
60), with at most three returned chunks per document.

Run the offline hashed-feature comparison on the development split:

```sh
make retrieval-eval
```

This writes per-query IDs, snippets, and scores plus JSON/Markdown Recall@1, Recall@3, Recall@5,
and MRR reports under `evals/reports/retrieval-dev/`. Query text comes from service names and
evidence summaries only; labels are used after retrieval to score cause-tagged relevant chunks.
The corpus relevance mapping is authored and synthetic, so the measurements compare these
fixtures and documents only.

For local semantic embeddings, install the optional extra and select the quantized BGE model:

```sh
uv sync --frozen --extra retrieval
make retrieval-eval RETRIEVAL_ARGS='--embedding-model BAAI/bge-small-en-v1.5'
```

FastEmbed runs model inference locally with ONNX Runtime. Its first use downloads the model into
the local cache; later runs use the cached weights. The benchmark report records the selected
model and embedding library. The 384-dimensional BGE model is also supported by the pgvector
schema. Reindex after changing embedding models.

PostgreSQL full-text and pgvector indexing are available after applying migration 0003. The local
Compose database uses the pgvector PostgreSQL 17 image. Run `make knowledge-reindex` to replace
the knowledge index, then use `make retrieval-eval RETRIEVAL_ARGS='--backend postgres'` to measure
the PostgreSQL backend. To index and measure semantic vectors, pass `--embedding-model
BAAI/bge-small-en-v1.5` to both commands. Reindexing replaces all rows in `knowledge_chunks`;
changing models requires a full reindex. PostgreSQL full-text search ORs sanitized query terms so
that it can return matches when only some observed evidence terms occur in a runbook.

The default vectors are deterministic hashed lexical features (`hash-ngram-v1`) for a reproducible
offline control. The generated [hash-feature report](../evals/reports/retrieval-dev/retrieval.md)
measured the checksummed development fixtures as follows:

| Method | Recall@1 | Recall@3 | Recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| Lexical | 0.0938 | 0.2188 | 0.5313 | 0.4969 |
| Vector, hashed features | 0.0781 | 0.2969 | 0.5104 | 0.5417 |
| Hybrid, RRF | 0.1094 | 0.3594 | 0.4688 | 0.5677 |

These values describe 16 authored development fixtures and 13 curated chunks. They do not
establish semantic-model quality or generalization. The BGE model path and PostgreSQL backend have
been exercised locally. The comparison results are linked below; all vectorizers execute locally
at inference time.

The measured reports cover the same 16 development fixtures and 13 chunks. Metrics are
Recall@1/Recall@3/Recall@5/MRR:

| Backend | Embedding | Lexical | Vector | Hybrid |
| --- | --- | --- | --- | --- |
| Memory | Hashed features | 0.0938/0.2188/0.5313/0.4969 | 0.0781/0.2969/0.5104/0.5417 | 0.1094/0.3594/0.4688/0.5677 |
| Memory | BGE small | 0.0938/0.2188/0.5313/0.4969 | 0.1406/0.3438/0.5313/0.5406 | 0.1406/0.3906/0.5156/0.5469 |
| PostgreSQL | Hashed features | 0.1719/0.3438/0.5313/0.5990 | 0.0781/0.2969/0.5104/0.5417 | 0.1875/0.3125/0.4844/0.6354 |
| PostgreSQL | BGE small | 0.1719/0.3438/0.5313/0.5990 | 0.1406/0.3438/0.5313/0.5406 | 0.1563/0.4219/0.5156/0.5833 |

The [hashed memory report](../evals/reports/retrieval-dev/hash-bge-comparison/retrieval.md),
[BGE memory report](../evals/reports/retrieval-dev/bge-memory/retrieval.md),
[hashed PostgreSQL report](../evals/reports/retrieval-dev/postgres-hash/retrieval.md), and
[BGE PostgreSQL report](../evals/reports/retrieval-dev/bge-postgres/retrieval.md) include
per-query rankings and snippets. BGE improves vector Recall@1 in this small corpus, while hybrid
MRR does not improve over hashed features. The authored judgments are synthetic; these numbers
compare the included fixtures and do not establish generalization or production search quality.
