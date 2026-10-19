# Evaluation dataset and deterministic baseline

The current slice provides frozen synthetic inputs and the S0 rules strategy. The benchmark runner, exact aggregate metrics, and JSON/Markdown benchmark reports are the next milestone. S1–S3, model comparisons, and diagnosis quality measurements have not been implemented or reported.

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

## Checks and limitations

36 new checks cover all 16 development contracts, input-order stability, freeze tampering and path escape, recipe reproducibility, abstention, invalid citations, same-pool matching, retry/fan-out distinction, revision requirements, competing causes, scope, and the CLI. Two of these checks use real simulator SDK output rather than authored fixture inputs. The project now has 148 passing offline tests using cached dependencies on Python 3.11.

The held-out fixtures are parsed for integrity but are not diagnosed by the tests. Future benchmark commands should default to `dev` and require an explicit final-report choice for `held_out`. Do not tune prompts after inspecting held-out outcomes without treating that set as development data and creating another holdout.

This small dataset is deliberately simple and balanced by fault family. It shares authored templates, has only one cause per case, has no healthy/no-incident cases, and its held-out split currently has no incomplete cases. Development checks do not demonstrate generalization, calibrated confidence, or production usefulness. A later dataset should add independently reviewed live captures, healthy controls, stronger confounders, and held-out incomplete cases under a new version. Cross-strategy reports must retain this version and disclose these gaps. No aggregate accuracy, latency, cost, retrieval, or agent-improvement results are claimed here.
