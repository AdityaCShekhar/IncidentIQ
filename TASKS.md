# IncidentIQ project tracker

Updated: 2026-10-19. Requirements and acceptance criteria are defined in [SPEC.md](SPEC.md).

Move a task between sections when its status changes. Completed means the listed artifact exists; execution checks are tracked separately so pending validation stays visible. Update this file at the end of each working session.

## In progress

| ID | Task | Completion criteria | Notes |
| --- | --- | --- | --- |
| S1V | Verify Session 1 in Docker and PostgreSQL | Build/start clean stack; migration succeeds; health and readiness return 200; database survives restart; integration test passes | Compose configuration passes. Execution blocked by Docker daemon permissions and socket restrictions. |
| LOCK | Resolve and commit dependency lock | Generate `uv.lock`; use frozen dependency installation for development and Docker; verify Python 3.12 install | Downloads fail because network DNS is unavailable. Current dependency ranges allow installation in a network-enabled environment. |
| S2V | Verify simulator and telemetry in Docker | Run baseline/fault comparison; inspect one trace across both services; observe metric change and exported logs; save measured demo artifact | Implementation and in-process SDK checks pass; Docker access remains denied. Collector/Prometheus runtime validators and actual backend delivery are pending. |
| S3V | Verify live evidence collection | Run scenario in Docker; collect the actual telemetry volume; resolve source locators; inspect slow inventory evidence and replay stable IDs | SDK-to-file-to-evidence test passes in process. Live Docker verification remains blocked by daemon permissions. |
| W2AV | Verify PostgreSQL API and volume access | Apply migration 0002 on PostgreSQL; run HTTP/evidence integration checks and row-lock behavior; verify Collector/API UID 10001 access after initialization | Offline route/ORM checks pass with SQLite. Expanded PostgreSQL test is authored and skipped without an explicit test database; Docker access remains unavailable. |
| W2BV | Verify deployment regression in Docker | Run baseline/fault/recovery; inspect healthy inventory and failing checkout traces; persist/filter deployment event; save actual demo artifact | In-process service/SDK/file/API checks pass. Live container and PostgreSQL checks remain pending because Docker access is unavailable. |
| W4 | Knowledge and hybrid retrieval | [Curated corpus](knowledge), [section chunking, local BGE embeddings, and retrieval backends](app/incidentiq/retrieval/knowledge.py), [Recall@k/MRR comparison](app/incidentiq/retrieval/evaluation.py), [CLI](scripts/evaluate_retrieval.py), pgvector migration 0003, and `make knowledge-reindex` / `make retrieval-eval` implemented. [Hashed-feature retrieval report](evals/reports/retrieval-dev/retrieval.md) generated after frozen-file checksum verification. Remaining: run the validated CLI, BGE semantic-model comparison, and PostgreSQL reindex/search when dependencies and Docker/PostgreSQL are available. |

## Not started

| ID | Task | Completion criteria | Dependencies |
| --- | --- | --- | --- |
| LLM | Structured summary baseline | Model adapter; typed output; recorded model/settings/token metadata; validated evidence references | W3B; follow prioritized build order after W4 |
| W5 | Bounded investigation workflow | Allowlisted read-only tools; 5 tool/3 model call caps; deadlines; one repair retry; persisted audits; RAG and agent strategies | W4, LLM |
| W6A | Fault and failure coverage | All four repeatable faults; injection resistance; malformed output/timeouts/tool/retrieval/telemetry failure tests | W5 |
| W6B | Comparative evaluation | S0–S3 results where supported; latency/resource costs; failure examples; measured improvement or documented lack thereof | W6A |
| W7 | Optional stretch work | Choose at most two only after core acceptance: reranker, Kafka, UI, jobs, second model | W6B |
| W8 | Portfolio packaging | Architecture, threat model, decisions, demo, limitations, measured results, clean-clone walkthrough | W6B |

## Completed

| ID | Task | Evidence |
| --- | --- | --- |
| SPEC | Save version 1 specification | [SPEC.md](SPEC.md), all 17 supplied sections |
| TRACK | Create persistent project tracker | This file; requested status categories and milestone dependencies |
| IGNORE | Git and Docker ignore rules | [.gitignore](.gitignore) excludes local credentials, Python/cache/build output, runtime files, and editor files while keeping fixtures and publishable reports versionable; [.dockerignore](.dockerignore) limits the build context to current Dockerfile inputs and excludes nested local artifacts. No tests or Docker build run for this configuration change. |
| S1 | Session 1 application foundation | [Application](app/incidentiq/main.py), [Compose](compose.yaml), [Dockerfile](Dockerfile), [configuration](app/incidentiq/core/config.py), [migration](migrations/versions/0001_create_incidents.py), [Makefile](Makefile), [README](README.md); runtime acceptance remains in S1V |
| S1U | Unit tests and static checks | 10 unit tests passed on Python 3.11 using cached dependencies; Ruff lint/format, Compose configuration, and offline Alembic SQL generation passed; integration test authored but execution pending in S1V |
| S2 | Slow dependency and telemetry implementation | [Simulator services](app/incidentiq/simulator/service.py), [SDK telemetry](app/incidentiq/simulator/telemetry.py), [scenario CLI](scripts/run_scenario.py), [Collector](telemetry/collector.yaml), [Prometheus](telemetry/prometheus.yaml), [demo instructions](docs/demo-script.md), [stack decision](docs/decisions/0001-local-first-stack.md); live acceptance remains in S2V |
| S2U | Simulator and telemetry tests | 21 new tests, 31 total passing on Python 3.11; real ASGI service chain, parent/child spans, correlated logs, observed slow span/histogram, bounded inputs, downstream failures, scenario validation, real SDK protobuf export with replaced Collector HTTP response; Ruff and Compose parsing pass |
| S3 | Deterministic evidence collector implementation | [Schemas](app/incidentiq/ingestion/schemas.py), [normalizer](app/incidentiq/ingestion/normalize.py), [bounded file adapter](app/incidentiq/ingestion/telemetry.py), [CLI](scripts/collect_evidence.py), read-only/no-network Compose job and `make evidence`; [usage and semantics](docs/evidence.md); persisted API integration is now implemented in W2A |
| S3U | Evidence and failure checks | 32 new tests, 63 total passing on Python 3.11; real simulator SDK exports normalize into stable slow-inventory evidence; time boundaries, rotation/deduplication, scope, malformed input, limits, deadline, symlinks, masking, CLI statuses and protected output paths; Ruff and Compose parsing pass |
| W2A | Incident API and evidence persistence implementation | [Routes](app/incidentiq/api/routes_incidents.py), [schemas](app/incidentiq/api/schemas.py), [repository](app/incidentiq/db/repositories.py), [models](app/incidentiq/db/models.py), [migration 0002](migrations/versions/0002_persist_evidence.py), [API walkthrough](docs/api.md); synchronous collection, atomic evidence/status, duplicate prevention, bounded filtered pages, safe errors, and unprivileged shared-volume access configured |
| W2AU | API and persistence checks | 34 new offline checks, 97 total passing; real file collector/HTTP/ORM tests with SQLite, persistence after source removal and app restart, atomic rollback, duplicate saves, incident isolation, filters/pages, input and failure handling; lint/format, Compose parsing, and offline upgrade/downgrade SQL generation pass; PostgreSQL execution remains in W2AV |
| W2B | Deployment regression scenario | [Bounded revision controls](app/incidentiq/simulator/deployments.py), [scenario runner](app/incidentiq/simulator/scenarios.py), [independently authored labels](simulator/scenarios/deployment_regression.json), structured deployment normalization and persisted `source=deployment` filtering; baseline checkout success, v2 response-contract errors with healthy inventory, explicit rollback/recovery; live acceptance remains in W2BV |
| W2BU | Deployment and evidence checks | 15 new offline checks, 112 total passing on Python 3.11; actual ASGI baseline/fault/recovery, healthy inventory, bounded revision metric dimensions, isolation/idempotency/capacity/expiry, rollback on response loss, malformed event rejection, stable deployment IDs, SDK-to-file-to-API persistence/filtering after source removal; Ruff checks and Compose parsing pass |
| W3A | Labelled evaluation dataset and deterministic rules | [24 frozen synthetic cases](evals/cases.jsonl), [expected evidence](evals/expected_evidence.jsonl), [checksums/splits](evals/manifest.json), [independent authoring recipe](scripts/author_eval_fixtures.py), [S0 rules](app/incidentiq/baselines/rules.py), shared typed reports/citation validation, `make dataset` and `make diagnose`; 16 dev/eight held-out cases, noise/incomplete variations and authored causal contracts; [design and limitations](docs/evaluation.md) |
| W3AU | Dataset and baseline checks | 36 new checks, 148 total offline tests passing on Python 3.11; all 16 dev contracts, deterministic ordering, fixture freeze/reproduction, scope/citation validation, abstention, confounder guards, mixed causes, and both actual simulator SDK evidence paths. Held-out strategies were not executed. Ruff lint/format pass. |
| W3B | Evaluation harness and reports | [Benchmark runner](scripts/run_benchmark.py), strategy boundary and comparable metrics [implementation](app/incidentiq/evaluation/benchmark.py), [CLI](app/incidentiq/evaluation/cli.py), `make benchmark`; preserves per-case raw reports and emits JSON/Markdown reports, defaults to dev and requires explicit held-out final-report flag. Execution is pending because uv cache writes are denied and the local virtualenv lacks Pydantic; offline dependency resolution cannot obtain the uncached OTLP package. |

## Current constraints

- This session cannot access `/var/run/docker.sock`; Docker build/start and PostgreSQL integration checks require a working Docker environment.
- Native PostgreSQL could initialize a temporary data directory but cannot create TCP or Unix sockets in this sandbox. Live database integration was not run.
- Package downloads fail DNS resolution. Dependency locking and clean Python 3.12 installation remain pending; unit tests used a temporary Python 3.11 environment with read-only cached packages.
- Aggregate benchmark results have not been measured. The 24 frozen cases contain authored synthetic observations, not measured captures. Pool-exhaustion and retry-storm live simulators, independent label review, and healthy/held-out incomplete cases remain pending; see evaluation limitations. Acceptance targets in the specification are goals.
- Offline SQLite checks do not verify PostgreSQL JSONB behavior, migration execution, or row locking. The PostgreSQL integration test is skipped until a disposable test database is supplied.
