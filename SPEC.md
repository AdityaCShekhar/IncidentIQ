# IncidentIQ — Applied AI / LLM Engineer Project Specification

**Version:** 1.0  
**Target build window:** 6–8 weeks  
**Primary developer environment:** Apple Silicon MacBook Pro; local-first development  
**Target role:** Applied AI / LLM Engineer  
**Project type:** Evidence-grounded incident investigation copilot

## 1. Product vision

IncidentIQ helps a backend engineer investigate a simulated production incident. It ingests telemetry and deployment events, retrieves relevant runbooks and prior incidents, calls a small set of read-only diagnostic tools, and returns a structured diagnosis with supporting evidence, uncertainty, and recommended next steps.

The project is successful only if it demonstrates that AI adds measurable value over deterministic rules and a plain LLM summary. It is not a generic chatbot, a dashboard-only project, or an autonomous production remediation agent.

### Target user story

As an on-call backend engineer, I want IncidentIQ to collect relevant evidence for an incident, identify likely causes, show which observations support or contradict each hypothesis, and recommend safe next diagnostic steps so that I can investigate faster without blindly trusting an LLM.

### Non-goals for v1

- Autonomous production writes, restarts, deployments, or remediation.
- Unrestricted shell, arbitrary SQL, or arbitrary network access from the agent.
- Multi-agent collaboration.
- Fine-tuning a foundation model.
- Kubernetes deployment or multi-tenant enterprise auth.
- A polished React frontend before the API and evaluation harness work.

## 2. Success criteria

By the end of the project, a reviewer should be able to clone the repository, run the stack locally, trigger a known fault, inspect telemetry, submit an incident ID, and compare multiple diagnosis strategies on the same cases.

Required deliverables:

1. Reproducible local environment with one command to start it.
2. At least four deterministic fault scenarios.
3. Incident API with persistent incident and investigation records.
4. Hybrid retrieval over runbooks and historical incidents.
5. Bounded tool-using investigation workflow with typed output.
6. Evidence references for every material diagnosis claim.
7. Evaluation dataset and executable benchmark report.
8. README with architecture, setup, demo script, limitations, and measured results.

Do not invent performance results. Report actual benchmark outcomes, including failures and limitations.

## 3. System architecture

```text
                 ┌──────────────────────────────┐
                 │ CLI / API client / optional UI│
                 └──────────────┬───────────────┘
                                │ HTTP
                 ┌──────────────▼───────────────┐
                 │ FastAPI: incidents, evidence,│
                 │ investigations, evaluations  │
                 └───────┬──────────────┬───────┘
                         │              │
                ┌────────▼──────┐  ┌────▼─────────────────┐
                │ PostgreSQL    │  │ Investigation runner│
                │ + pgvector    │  │ bounded state machine│
                └────────┬──────┘  └────┬─────────────────┘
                         │              │
               ┌─────────▼────────┐  ┌──▼────────────────────┐
               │ Hybrid retrieval │  │ Read-only tool layer  │
               │ lexical + vector│  │ traces/logs/metrics/  │
               │ + rerank optional│  │ deploys/runbooks      │
               └──────────────────┘  └──┬────────────────────┘
                                         │
                              ┌──────────▼──────────┐
                              │ Model adapter       │
                              │ local Ollama or API │
                              └─────────────────────┘

     Fault simulator / sample services ── OpenTelemetry ──> telemetry backend
```

### Main components

- **Simulator:** Small service(s) that expose normal and faulty behavior. Emit structured logs, traces, and metrics.
- **Telemetry adapter:** Reads a bounded incident window from the telemetry backend and normalizes observations into evidence records.
- **Incident API:** Creates incidents, returns status/evidence, starts investigations, and serves results.
- **Evidence normalizer:** Turns raw telemetry into typed records with timestamps, source, service, severity, and stable IDs.
- **Knowledge ingestion:** Loads runbooks and past incident reports; chunks documents; stores text, metadata, and embeddings.
- **Hybrid retriever:** Combines lexical and vector results; deduplicates and returns source IDs and snippets.
- **Tool layer:** Explicit read-only functions with schemas, limits, and timeouts.
- **Investigation runner:** Bounded state machine that chooses tools, gathers evidence, drafts hypotheses, checks evidence coverage, and finalizes.
- **Model adapter:** Common interface for local and hosted models; records model/version/configuration with every run.
- **Evaluation runner:** Replays labelled cases against several baselines and writes machine-readable results.

## 4. Core workflow

1. User creates or selects an incident and specifies a time window and affected service.
2. API loads incident metadata and a bounded evidence bundle.
3. Runner queries read-only tools for relevant traces, errors, latency, saturation, deployment changes, and dependencies.
4. Retriever searches runbooks and prior incidents using service names, symptoms, error phrases, and time-window context.
5. Model produces a typed hypothesis list with supporting evidence IDs, contradictory evidence IDs, confidence, and missing information.
6. A deterministic validator rejects references to nonexistent evidence IDs and checks required fields.
7. If evidence is insufficient, the runner may perform a limited additional investigation step; otherwise it abstains or reports low confidence.
8. API persists the investigation, model metadata, tool calls, retrieved documents, latency, and token/cost data where available.
9. User receives a concise report with likely cause, supporting observations, alternatives, safe next steps, and explicit uncertainty.

### Bounded orchestration rules

- Maximum 5 tool calls per investigation in v1.
- Maximum 3 model calls per investigation in v1.
- Per-tool timeout and total investigation deadline.
- Tool allowlist only; model cannot construct arbitrary URLs, SQL, or shell commands.
- Validate every tool argument with Pydantic.
- On model timeout or malformed output, retry at most once with a constrained repair prompt; then fail clearly or return partial results.
- No automatic state-changing actions.

## 5. Fault simulator and benchmark cases

Implement at least four faults. Each scenario must be repeatable and have ground-truth labels.

1. **Slow downstream dependency:** Latency increases; upstream request latency rises; errors may remain low.
2. **Database connection-pool exhaustion:** Pool wait time and request latency rise; errors/timeouts follow.
3. **Deployment regression:** A deployment event precedes a change in error rate or latency for one service.
4. **Retry storm:** Repeated downstream calls amplify load and latency.

Stretch scenarios: Kafka consumer lag, cache outage, malformed configuration, elevated memory/CPU, and an unrelated noisy alert to test distraction resistance.

For each case store:

- Case ID, scenario name, seed/configuration, incident time window.
- Primary root cause and acceptable aliases.
- Affected services and dependency chain.
- Expected evidence IDs or evidence predicates.
- Plausible alternative causes.
- Recommended safe next diagnostic step.
- Severity and expected abstention behavior if evidence is intentionally incomplete.

Do not rely on the LLM to generate ground truth. Ground truth must be authored independently of the model output.

## 6. API contract (v1)

### `POST /incidents`

Create an incident record.

Request fields: `title`, `service`, `started_at`, `ended_at`, `severity`, optional `scenario_id`, optional `description`.

Response: incident object with stable ID and status.

### `GET /incidents/{incident_id}`

Return incident metadata and processing status.

### `GET /incidents/{incident_id}/evidence`

Return normalized evidence records for the incident window. Support filters for service, source, and severity with strict limits.

### `POST /incidents/{incident_id}/investigations`

Start an investigation. Request includes strategy (`rules`, `llm_summary`, `rag`, `agent`) and optional model profile. Return investigation ID and status. In early versions this may run synchronously; add a background job only if execution duration makes it necessary.

### `GET /investigations/{investigation_id}`

Return status, final structured diagnosis, evidence references, tool-call summary, and model metadata.

### `POST /knowledge/reindex`

Local/dev-only endpoint or CLI command to ingest documents. Do not expose unauthenticated in a public deployment.

### `POST /evaluations/runs`

Run selected benchmark cases and strategies. For v1, a CLI command is acceptable instead of a public endpoint.

Use consistent error responses, request IDs, pagination/limits, UTC timestamps, and explicit timeout behavior. Do not build more endpoints until the core end-to-end path works.

## 7. Data model

### `incidents`

- `id`: UUID primary key.
- `title`, `service`, `severity`, `description`.
- `started_at`, `ended_at`: timestamps.
- `scenario_id`: nullable.
- `created_at`.

### `evidence_records`

- `id`: UUID primary key.
- `incident_id`: foreign key.
- `source_type`: `log`, `trace`, `metric`, `deployment`, `runbook`, `prior_incident`.
- `service`, `timestamp`, `severity`.
- `summary`, `payload`: JSONB payload.
- `source_uri` or stable source locator.
- `content_hash` for deduplication.

### `knowledge_chunks`

- `id`: UUID primary key.
- `document_id`, `chunk_index`, `text`, `metadata`: JSONB metadata.
- `embedding`: vector.
- `content_hash`, `created_at`.

### `investigations`

- `id`: UUID primary key.
- `incident_id`: foreign key.
- `strategy`, `status`, `model_name`, `model_config`: JSONB model configuration.
- `result`: JSONB, `error_code`, `error_summary`.
- `started_at`, `completed_at`, `latency_ms`.
- `prompt_tokens`, `completion_tokens`, `estimated_cost`: nullable.

### `tool_calls`

- `id`: UUID primary key.
- `investigation_id`: foreign key.
- `tool_name`, `input`: JSONB input, `output_summary`: JSONB.
- `status`, `latency_ms`, `error_code`.

### `evaluation_results`

- `id`: UUID primary key.
- `run_id`, `case_id`, `strategy`, `model_name`.
- `predicted_root_cause`, `expected_root_cause`, `metrics`: JSONB metrics.
- `raw_result`: JSONB, `created_at`.

Keep sensitive raw data out of logs. Use synthetic telemetry in the public demo.

## 8. Structured AI output

Use Pydantic schemas rather than parsing free-form prose.

```python
from pydantic import BaseModel, Field
from typing import Literal


class Hypothesis(BaseModel):
    cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    explanation: str


class InvestigationReport(BaseModel):
    incident_summary: str
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    missing_evidence: list[str]
    recommended_next_steps: list[str] = Field(max_length=5)
    disposition: Literal["likely_cause_found", "insufficient_evidence", "inconclusive"]
```

After model generation, validate that every evidence ID exists in the investigation's evidence bundle. Reject or repair invalid references; never silently accept fabricated IDs. A confidence score is a model-reported estimate, not a calibrated probability; evaluate calibration separately if time permits.

## 9. Retrieval design

Start with a small, deliberately curated corpus: runbooks, service ownership notes, architecture summaries, and labelled historical incident reports.

1. Normalize documents and attach metadata: service, subsystem, tags, document type, last-updated date.
2. Chunk by headings/semantic sections rather than blindly splitting every N characters; retain source and section metadata.
3. Generate embeddings locally and persist vectors in pgvector.
4. Run lexical retrieval and vector retrieval independently.
5. Merge with reciprocal-rank fusion or a simple, documented score combination.
6. Deduplicate by document/chunk and cap results per document.
7. Return snippets with stable source IDs, not only raw text.
8. Evaluate retrieval before adding reranking. Add a small cross-encoder reranker only if baseline results justify it.

Do not add a separate vector database unless PostgreSQL proves insufficient for the benchmark scale.

## 10. Evaluation plan

Evaluation is a first-class feature, not a final polish step. Build the dataset and rules baseline before building the agent.

### Strategies to compare

- **S0 — Rules baseline:** Deterministic symptom-to-cause heuristics using telemetry features.
- **S1 — LLM summary:** Provide a compact evidence bundle, no retrieval or tools.
- **S2 — RAG:** Evidence bundle plus retrieved runbooks/prior incidents.
- **S3 — Tool-using agent:** Bounded investigation, retrieval, and read-only tool calls.

All strategies must run against the same labelled cases and have the same ground-truth definitions.

### Metrics

- **Root-cause top-1 accuracy:** Primary cause correctly identified.
- **Root-cause top-3 recall:** Expected cause appears among up to three hypotheses.
- **Evidence precision:** Cited evidence that actually supports the associated claim / all cited evidence.
- **Evidence coverage/recall:** Expected relevant evidence retrieved or cited / expected evidence.
- **Retrieval Recall@k and MRR:** Whether relevant chunks appear near the top of retrieval results.
- **Unsupported-claim rate:** Material claims without valid supporting evidence / all material claims sampled.
- **Abstention quality:** Appropriate abstention when evidence is incomplete and avoidance of unnecessary abstention when evidence is sufficient.
- **Tool success rate:** Successful valid calls / attempted calls.
- **Latency:** Median and p95 end-to-end time, separating retrieval, model, and tool latency.
- **Cost/resource use:** Token counts for hosted models; local inference time and memory observations where practical.

### Evaluation discipline

- Use at least 20–30 labelled cases for an initial meaningful comparison; include variations and incomplete/noisy cases, not only four happy-path demos.
- Freeze the case set before tuning prompts against it. Keep a small held-out set for final reporting.
- Run each model-based case more than once if outputs are stochastic; report variance where practical.
- Use deterministic code for exact metrics. Use an LLM judge only as a supplementary metric and manually inspect a sample.
- Save raw outputs, prompts/templates version, model name/version, decoding settings, retrieved chunk IDs, and tool-call traces.
- Publish results with a limitations section. Do not claim statistical significance from a tiny dataset.

### Minimum acceptance targets

These are project goals, not pre-claimed results:

- Every returned evidence reference resolves to an evidence record.
- No tool call can escape the allowlist or exceed configured bounds.
- All four core fault scenarios are reproducible.
- At least 20 labelled evaluation cases run end-to-end.
- Evaluation report compares S0–S3 where supported and includes failure examples.
- Agent demonstrates an improvement over at least one simpler baseline on a defined metric, or clearly documents why it does not.
- Invalid model output, model timeout, retrieval failure, and telemetry unavailability are tested.

## 11. Repository structure

```text
incidentiq/
├── README.md
├── SPEC.md
├── Makefile
├── compose.yaml
├── .env.example
├── pyproject.toml
├── alembic.ini
├── migrations/
├── docs/
│   ├── architecture.md
│   ├── threat-model.md
│   ├── evaluation.md
│   ├── demo-script.md
│   └── decisions/
│       ├── 0001-local-first-stack.md
│       └── 0002-bounded-state-machine.md
├── app/
│   └── incidentiq/
│       ├── main.py
│       ├── api/
│       │   ├── routes_incidents.py
│       │   ├── routes_investigations.py
│       │   └── schemas.py
│       ├── core/
│       │   ├── config.py
│       │   ├── logging.py
│       │   └── errors.py
│       ├── db/
│       │   ├── session.py
│       │   ├── models.py
│       │   └── repositories.py
│       ├── ingestion/
│       │   ├── telemetry.py
│       │   ├── normalize.py
│       │   └── knowledge.py
│       ├── retrieval/
│       │   ├── lexical.py
│       │   ├── vector.py
│       │   ├── hybrid.py
│       │   └── schemas.py
│       ├── tools/
│       │   ├── registry.py
│       │   ├── logs.py
│       │   ├── traces.py
│       │   ├── metrics.py
│       │   └── deployments.py
│       ├── agent/
│       │   ├── state.py
│       │   ├── runner.py
│       │   ├── prompts.py
│       │   ├── output.py
│       │   └── validators.py
│       ├── models/
│       │   ├── base.py
│       │   ├── ollama.py
│       │   └── hosted.py
│       └── evaluation/
│           ├── dataset.py
│           ├── metrics.py
│           ├── runners.py
│           └── report.py
├── simulator/
│   ├── services/
│   ├── scenarios/
│   └── seeds/
├── knowledge/
│   ├── runbooks/
│   └── incidents/
├── evals/
│   ├── cases.jsonl
│   ├── expected_evidence.jsonl
│   └── reports/
├── scripts/
│   ├── seed_db.py
│   ├── ingest_knowledge.py
│   ├── run_scenario.py
│   └── run_eval.py
└── tests/
    ├── unit/
    ├── integration/
    ├── contract/
    ├── security/
    └── evaluation/
```

This is a target structure, not a requirement to create empty modules on day one. Add a package only when the first working slice needs it.

## 12. Six-to-eight-week implementation milestones

### Week 1 — Reproducible incident and telemetry

**Build:** Repository, Docker Compose, one small API/service, structured logs, OpenTelemetry traces, basic metrics, and one slow-dependency fault.

**Exit criteria:** Trigger the fault on demand; see the latency change in telemetry; correlate one request across services; save a short demo trace or screenshot in docs.

**Do not build:** RAG, agents, frontend, Kubernetes.

### Week 2 — Evidence model and incident API

**Build:** PostgreSQL schema/migrations, incident and evidence records, telemetry-window collector, normalization, and API endpoints. Add database-pool exhaustion or deployment regression as the second scenario.

**Exit criteria:** An incident ID resolves to a deterministic evidence bundle with stable IDs; API and integration tests pass; failure modes for missing telemetry and invalid windows are explicit.

### Week 3 — Baselines and evaluation dataset

**Build:** Rules baseline, compact LLM-summary baseline, 20–30 labelled cases, metrics scripts, and JSON/Markdown report output.

**Exit criteria:** One command runs the baselines and produces comparable results. The evaluation harness exists before agent orchestration.

### Week 4 — Knowledge ingestion and hybrid retrieval

**Build:** Curated runbooks and historical incident corpus, chunking, local embeddings, pgvector, PostgreSQL full-text search, hybrid rank fusion, retrieval metrics.

**Exit criteria:** Recall@k/MRR measured; retrieved chunks include stable IDs and source metadata; irrelevant or stale documents can be inspected and corrected.

### Week 5 — Bounded investigation workflow

**Build:** Explicit state machine, tool registry, tool schemas, timeouts, call budgets, structured report, evidence-ID validation, and graceful abstention.

**Exit criteria:** The agent investigates at least three scenarios end-to-end; malformed output and tool failure do not crash the API or create unsupported evidence references.

### Week 6 — Hardening and comparative evaluation

**Build:** Remaining fault scenarios, negative/incomplete cases, injection-resistant knowledge handling, latency tracking, model adapter, local model benchmark, and optional hosted comparison.

**Exit criteria:** S0–S3 comparison exists where runnable; failures are categorized; safety tests verify no arbitrary SQL/shell/network calls; report includes latency and quality trade-offs.

### Week 7 — Optional stretch work

Choose only one or two: cross-encoder reranking, Kafka event ingestion, simple React investigation view, background job execution, or a second model comparison. Do not sacrifice benchmark quality for UI.

### Week 8 — Portfolio packaging and interview readiness

**Build:** Architecture and threat-model docs, demo script, clean setup, sample output, benchmark charts/tables, known limitations, and a concise technical write-up.

**Exit criteria:** A reviewer can run the demo from a clean clone using documented commands; the project has no fabricated metrics, secrets, or hidden manual steps.

## 13. Prioritized build order

Use this order even if the repository structure above lists more components:

1. One fault that can be reproduced locally.
2. Traces/metrics/logs that prove what happened.
3. Normalized evidence bundle with stable IDs.
4. Incident API and persistence.
5. Rules baseline and labelled cases.
6. Evaluation harness and report.
7. Knowledge ingestion and retrieval benchmark.
8. Structured LLM summary baseline.
9. Bounded tool-using investigation workflow.
10. Evidence validation, abstention, and failure handling.
11. Additional fault scenarios and security tests.
12. Optional UI, reranker, Kafka, or hosted-model comparison.

If you fall behind, cut in this order: UI, Kafka, reranker, background workers, extra model providers. Do not cut the evidence model, evaluation harness, or failure handling.

## 14. Safety and threat model

- Treat logs, stack traces, runbooks, incident reports, and deployment metadata as untrusted input; they may contain prompt-injection strings.
- The model may summarize and select among pre-registered tools, but tool execution is validated and controlled by application code.
- Never interpolate model output into SQL or shell commands.
- Read-only telemetry and retrieval tools only in v1.
- Apply timeouts, result limits, payload truncation, and per-investigation budgets.
- Keep secrets out of prompts, logs, evaluation fixtures, and public examples.
- Separate instructions from retrieved content; explicitly tell the model retrieved text is data, not instructions.
- Validate output schema and evidence IDs deterministically.
- Return `insufficient_evidence` or `inconclusive` when evidence does not support a reliable conclusion.
- Future remediation actions must require explicit human approval and be implemented as separately authorized application operations.

## 15. Local developer workflow

Suggested commands (implement these as the project evolves):

```sh
make up             # start database, telemetry and app dependencies
make migrate        # apply database migrations
make seed           # load synthetic knowledge and benchmark fixtures
make scenario NAME=slow_dependency
make test           # unit and integration tests
make eval           # run the benchmark suite
make report         # render evaluation results
make down           # stop local services
```

Keep `.env.example` safe to commit. Document local model setup separately from the default stack so users without a compatible model runtime can still run the deterministic baseline and tests.

## 16. Definition of done

The flagship project is ready to present when:

- [ ] A clean clone can run the deterministic demo using documented commands.
- [ ] Four core failure scenarios are repeatable.
- [ ] Evidence is normalized, persisted, and traceable to a source.
- [ ] Lexical, vector, and hybrid retrieval are evaluated.
- [ ] Rules, LLM summary, RAG, and agent strategies can be compared fairly where supported.
- [ ] Every material diagnosis includes evidence references or explicitly states that evidence is missing.
- [ ] Unsupported references, model timeouts, malformed output, tool failures, and retrieval failures are covered by tests.
- [ ] Tool access is read-only, allowlisted, bounded, and audited.
- [ ] Benchmark results are real, reproducible, and accompanied by limitations.
- [ ] README includes architecture, setup, example output, trade-offs, and demo steps.

## 17. First three working sessions

**Session 1:** Create the repository and Compose stack; run a tiny FastAPI service and PostgreSQL; verify migrations and health checks.

**Session 2:** Add a downstream dependency endpoint with configurable delay; instrument one request with OpenTelemetry; demonstrate that latency changes in the trace and metric data.

**Session 3:** Write a collector that turns one incident window into a deterministic JSON evidence bundle; add a test proving the expected slow dependency appears in that bundle.

At the end of these sessions, you should have a real observable backend failure—not a chatbot. That is the foundation for every AI feature that follows.
