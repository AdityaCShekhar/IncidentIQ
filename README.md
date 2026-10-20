# IncidentIQ

IncidentIQ is an evidence-grounded incident investigation copilot being built against [SPEC.md](SPEC.md). It will investigate synthetic backend faults using telemetry, retrieval, and bounded diagnostic tools, with a benchmark comparing rules, LLM summary, RAG, and agent strategies.

The current implementation includes persistent incident/evidence API routes, two deterministic faults (slow dependency and deployment regression), correlated telemetry, bounded collection with stable evidence IDs, and an evidence-citing rules baseline with 24 frozen synthetic evaluation cases. Progress and pending verification are recorded in [TASKS.md](TASKS.md).

## Run locally

Install Docker with the Compose plugin (Docker Desktop on Apple Silicon) and Make. The default stack does not require Python, a model runtime, or API credentials on the host.

```sh
make up
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready
```

`make up` builds the app image, starts PostgreSQL, applies migrations, starts the simulator and telemetry services, and waits for API/simulator health checks. The API is at <http://localhost:8000>; interactive API documentation is at <http://localhost:8000/docs>. Services bind to the host loopback interface. Images use their native architecture without a forced amd64 platform.

Configuration is optional: copy `.env.example` to `.env` before starting to override development defaults. If port 5432 or 8000 is occupied, change `POSTGRES_PORT` or `APP_PORT`. Changing PostgreSQL credentials after the database volume has been initialized does not change existing database roles; keep the credentials matching that volume.

```sh
make ps       # service status, including the completed migration
make logs     # follow service logs
make migrate  # explicitly apply migrations to the running database
make down     # stop services; preserve PostgreSQL data
```

Use `docker compose down --volumes` only when you intend to delete all local database data. These credentials are for synthetic local development.

## Slow dependency demo

The scenario command requires Python and uv as described below. After `make up`:

```sh
make telemetry-config
make scenario NAME=slow_dependency
```

The scenario sends baseline and delayed requests through `checkout → inventory`, then saves measured client latencies and trace IDs to `docs/demos/slow-dependency.json`. Inspect traces at <http://localhost:16686> and metrics at <http://localhost:9090>. To start only the simulator and telemetry services, run `make simulator-up`.

See [the demo walkthrough](docs/demo-script.md) for custom delays, metric queries, log correlation, failure behavior, and verification limits.

## Deployment regression demo

```sh
make scenario NAME=deployment_regression
# Allow a few seconds for telemetry export, then:
make evidence NAME=deployment_regression > docs/demos/deployment-regression-evidence.json
```

The scenario measures baseline checkout success, activates a faulty response formatter, observes checkout HTTP 500 responses while inventory succeeds, and rolls back to verify recovery. It saves actual phase windows and request/trace/deployment IDs to `docs/demos/deployment-regression.json`. Revision changes are scoped to one run ID. Independently authored labels and expected evidence predicates are in [the scenario definition](simulator/scenarios/deployment_regression.json).

Structured deployment events become evidence records with `source_type=deployment`, persisted through the existing incident API. See [the deployment walkthrough](docs/demo-script.md#deployment-regression) for controls, cleanup, and limitations.

## Collect evidence

After running the scenario, allow a few seconds for telemetry export, then collect its fault window:

```sh
make evidence > docs/demos/slow-dependency-evidence.json
```

The command reads the telemetry volume through a read-only container with networking disabled. Its JSON bundle includes normalized traces/logs/metrics, stable evidence UUIDs, source locators, coverage counts, and explicit partial or missing-data status. Fault labels and delay configuration are excluded from the evidence. See [evidence collection](docs/evidence.md) for local-file commands, limits, exit codes, and cumulative metric semantics.

## Incident API

- `POST /incidents` creates an incident, collects its bounded telemetry window, and saves evidence and coverage status.
- `GET /incidents/{id}` returns persisted incident metadata and collection status.
- `GET /incidents/{id}/evidence` returns saved records with service/source/severity filters and pagination (at most 100 records per page).

Creation returns HTTP 201 and a stable UUID even if telemetry is missing; inspect `status`, `evidence_count`, and `collection.status` before using the results. Evidence remains available after raw files rotate or the API restarts. See [the API walkthrough](docs/api.md) to create an incident from the saved scenario window and query its evidence.

## Dataset and rules baseline

After installing development dependencies, validate the frozen cases and diagnose one evidence bundle:

```sh
make dataset
make diagnose EVIDENCE=evals/fixtures/iq-001.json
```

S0 returns a typed report with evidence references and abstains when no supported pattern matches. The dataset has 16 development and eight held-out cases; the latter are reserved for final reporting. All fixture numbers are authored synthetic inputs. Pool-exhaustion and retry-storm live simulators are still pending. See [evaluation design and limitations](docs/evaluation.md) for the label boundary, freeze checks, rule thresholds, and commands for actual simulator bundles.

Run the rules benchmark on the development split with `make benchmark`. It writes per-case raw
reports plus aggregate JSON and Markdown under `evals/reports/dev-rules/`. Held-out scoring
requires the explicit `--split held_out --final-report` flags. Metrics and their interpretation
are documented in [evaluation design](docs/evaluation.md).

## Knowledge retrieval

The curated `knowledge/` corpus is split by Markdown section and assigned stable chunk IDs. Run
the development lexical, vector, and hybrid retrieval comparison with `make retrieval-eval`;
reports go to `evals/reports/retrieval-dev/`. For PostgreSQL full-text and pgvector, start the
stack, apply migrations, run `make knowledge-reindex`, then run
`make retrieval-eval RETRIEVAL_ARGS='--backend postgres'`. For semantic vectors, install the
optional retrieval dependency with `uv sync --extra retrieval`, then pass
`--embedding-model BAAI/bge-small-en-v1.5` to both commands. Model inference runs locally after its
first-use download. See [retrieval evaluation details](docs/evaluation.md).

## Application development

Install Python 3.12 (3.11 is also supported) and [uv](https://docs.astral.sh/uv/getting-started/installation/). Dependency ranges are declared in `pyproject.toml`. A committed dependency lock and frozen installation commands are pending network-enabled dependency resolution; this foundation does not yet guarantee identical dependency versions across installs.

```sh
make sync
make test
make lint
```

To run the API on the host, start only the database and apply migrations before starting the development server:

```sh
docker compose up -d --wait db
uv run alembic upgrade head
make dev
```

For host commands, set `DATABASE_PORT` to match `POSTGRES_PORT` if you changed the published database port. Compose always uses the internal database port 5432.

Integration tests require an explicitly configured, disposable PostgreSQL database. They apply and downgrade migrations and must never target a database containing useful data:

```sh
docker compose exec db createdb -U incidentiq incidentiq_test
INCIDENTIQ_TEST_DATABASE_URL='postgresql+psycopg2://incidentiq:incidentiq_local_only@127.0.0.1:5432/incidentiq_test' make test-integration
```

Adjust this example for your configured user, password, and published port. Tests refuse to run against a database whose name does not end in `_test`.

## Current architecture

```text
HTTP client -> FastAPI -> PostgreSQL 17 (incidents and evidence)
                  |          ^
             health checks   |
                        Alembic migration

Scenario CLI -> checkout -> inventory
                  |            |
                  +-- OpenTelemetry --+
                                     |
                                  Collector
                                  /   |   \
                              Jaeger  |  OTLP JSON files -> Evidence collector -> API persistence / JSON
                                  Prometheus
```

Liveness checks the API process. Readiness checks PostgreSQL connectivity and the expected migration revision, returning HTTP 503 when either is unavailable. Database connections, pool waits, and readiness SQL have bounded timeouts. Request logs contain a UTC timestamp, request ID, status, and duration; request bodies, query parameters, and database exception details are omitted.

Compose waits for database health and successful migration completion before starting the API, following [Docker's startup dependency guidance](https://docs.docker.com/compose/how-tos/startup-order/).

## Validation status

148 offline tests passed before the benchmark and retrieval harnesses were added, covering API health/errors, both fault scenarios through the real ASGI service chain, telemetry exporters and evidence collection, stable IDs and limits, and persistent incident/evidence HTTP behavior using SQLite ORM tests. Dataset and baseline checks cover 16 development contracts, frozen inputs, reproducible fixtures, citation validation, abstention, competing causes, and actual simulator SDK evidence. Held-out cases are validated for integrity without running a diagnosis strategy. Ruff lint/format checks pass; Compose parsing and offline migration SQL generation passed in preceding slices. The benchmark command has not executed because required dependencies are unavailable. The in-memory hashed-feature retrieval metrics are saved under `evals/reports/retrieval-dev/`; BGE model inference and PostgreSQL retrieval remain unverified.

Tests used cached packages on Python 3.11 because package downloads were unavailable. PostgreSQL is the application backend; SQLite is used only for offline ORM checks. The expanded PostgreSQL integration test is skipped without an explicit disposable test database. Container build/start, live PostgreSQL migration and row locking, telemetry-volume ownership, backend configuration and delivery, and saved Docker demo artifacts remain pending. This execution sandbox denies Docker daemon access and PostgreSQL socket creation. See [TASKS.md](TASKS.md) for the remaining acceptance checks.

## Scope and next session

The next slice adds the executable evaluation harness and JSON/Markdown benchmark reports for S0, with explicit availability reporting for S1–S3. Retrieval, model inference, and investigation tools are pending. No aggregate diagnosis quality or performance benchmark results have been measured.
