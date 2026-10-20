# Evaluation dataset and deterministic baseline

The current slice provides frozen synthetic inputs, the S0 rules strategy, and a deterministic benchmark runner. S1–S3 and model comparisons have not been implemented.

## Dataset v1

| Property | Contents |
| --- | --- |
| Version | `fixture-v1` |
| Provenance | `authored_synthetic_fixture`; synthetic numbers, not measured telemetry |
| Cases | 24; six per fault family |
| Fault families | Slow dependency, database-pool exhaustion, deployment regression, retry storm |
| Development split | 16 cases, including four incomplete cases that should abstain |
| Held-out split | Eight cases, reserved for final reporting; strategy outputs not inspected |
| Files | `evals/cases.jsonl`, `evals/expected_evidence.jsonl`, `evals/fixtures/*.json` |
| Freeze | SHA-256 checksums in `evals/manifest.json` |

Each family has clear, early/shorter, unrelated-noise, missing-evidence, instruction-text, and service-name variations. Instruction-text and service-name variations are held out. Every case specifies a seed, causal configuration, incident window, primary cause and aliases, affected services and dependency chain, alternatives, a safe diagnostic step, severity, and expected abstention behavior. Expectations separately identify relevant and irrelevant evidence UUIDs, observation roles, and evidence predicates.

Ground-truth labels come from the declared fixture mechanisms in [the authoring recipe](../scripts/author_eval_fixtures.py), independently of diagnosis outputs. The recipe does not import or invoke a diagnosis strategy or model. It constructs observations, passes them through the existing evidence normalizer, and writes labels separately. These are repository-authored contracts, not labels derived from an LLM judge, and have not received an independent human audit.

The pool and retry examples are fixture contracts for simulators that are still pending. Their presence does not establish that those faults can be reproduced over HTTP. Slow-dependency and deployment-regression simulator output is separately checked against S0 using the real in-process service chain and SDK exports. Docker/backend delivery remains unverified in this environment.

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

Spans are joined by trace ID and parent span ID, and client intervals must fit inside their parent. Rules scope metrics and deployment events to the affected service. Cumulative metric snapshots do not establish pool saturation; only the named gauges are accepted. Pool/retry diagnostic attributes use project-owned names: `incidentiq.pool.name` and `incidentiq.retry_attempt`. Those two live emitters are pending.

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

Output records strategy/version, incident UUID, `model: null`, and the typed report. Exit zero means the computation succeeded, including an abstention; inspect `report.disposition`. Invalid evidence, references, scope, or file operations fail with a safe error and nonzero status. The CLI rejects an output path that would overwrite its input. The diagnosis is not persisted as an investigation yet; investigation API/storage comes later.

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

## Checks and limitations

36 checks cover the dataset and rules contracts. The benchmark emits exact aggregate metrics, metadata, raw outputs, and Markdown/JSON summaries. Two existing strategy checks use real simulator SDK output rather than authored fixture inputs. The project had 148 passing offline tests before the benchmark harness was added.

The held-out fixtures are parsed for integrity but are not diagnosed by tests. The benchmark command defaults to `dev` and requires an explicit final-report choice for `held_out`. Do not tune prompts after inspecting held-out outcomes without treating that set as development data and creating another holdout.

This small dataset is deliberately simple and balanced by fault family. It shares authored templates, has only one cause per case, has no healthy/no-incident cases, and its held-out split currently has no incomplete cases. Development checks do not demonstrate generalization, calibrated confidence, or production usefulness. A later dataset should add independently reviewed live captures, healthy controls, stronger confounders, and held-out incomplete cases under a new version. Cross-strategy reports must retain this version and disclose these gaps. No aggregate accuracy, latency, cost, retrieval, or agent-improvement results are claimed here.

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
uv sync --extra retrieval
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
changing models requires a full reindex.

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
not been run in this environment. Both vectorizers execute locally at inference time.
