# IncidentIQ

IncidentIQ is an evidence-grounded incident investigation copilot being built against [SPEC.md](SPEC.md). It investigates synthetic backend faults using telemetry, retrieval, and bounded diagnostic tools, with a benchmark comparing rules, LLM summary, RAG, and agent strategies.

The core demo runs through Docker Compose. Ollama is an optional local dependency for model-backed investigations and benchmarks; it is not included in the Compose stack. The project focuses on evidence quality, bounded investigation workflows, and measured comparisons across diagnosis strategies.

The current implementation includes persistent incident, evidence, and investigation APIs; four deterministic fault scenarios; correlated telemetry; bounded collection with stable evidence IDs; hybrid knowledge retrieval; and an evaluation harness with 24 frozen synthetic cases. Progress and pending verification are recorded in [TASKS.md](TASKS.md).

For concise, evidence-based resume bullets and the claims they require you to qualify, see the [resume summary](docs/resume-summary.md).

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

## Clean-clone walkthrough

This walkthrough uses Docker for the services and Python/uv for scenario and API client commands. It does not require Ollama. Start from a fresh clone with Docker Compose, Make, Python 3.12 (3.11 is also supported), and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
make up
curl -fsS http://localhost:8000/health/ready
make sync
make scenario NAME=slow_dependency SCENARIO_ARGS='--output docs/demos/clean-clone-slow-dependency.json'
sleep 3
make evidence NAME=slow_dependency SCENARIO_FILE=clean-clone-slow-dependency > /tmp/incidentiq-evidence.json
```

The scenario command records baseline and fault windows and emits traces, logs, and metrics. The evidence command reads the generated fault window through the read-only collector job. It writes evidence JSON to `/tmp`; its status and record count are printed to stderr. The additional scenario artifact is in `docs/demos/clean-clone-slow-dependency.json` so it does not replace the checked-in example.

Create an incident request from that measured fault window and post it to the API:

```sh
uv run --frozen python - <<'PY' > /tmp/incidentiq-request.json
import json
from pathlib import Path
from incidentiq.ingestion.cli import window_from_scenario

window = window_from_scenario(
    Path("docs/demos/clean-clone-slow-dependency.json"), "fault", "checkout", ["inventory"]
)
request = window.model_dump(mode="json")
request.update(title="Clean-clone latency investigation", severity="high", scenario_id="slow_dependency")
print(json.dumps(request))
PY

curl -fsS -X POST http://localhost:8000/incidents \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/incidentiq-request.json
```

Copy the returned incident `id` into these commands to inspect persisted evidence and run the deterministic rules strategy:

```sh
curl -fsS 'http://localhost:8000/incidents/INCIDENT_ID/evidence?limit=20'
curl -fsS -X POST http://localhost:8000/incidents/INCIDENT_ID/investigations \
  -H 'Content-Type: application/json' \
  -d '{"strategy":"rules"}'
```

The investigation response includes its status, diagnosis, evidence references, and persisted ID. This demonstrates the local API workflow; it does not validate model-backed diagnosis quality. Inspect traces at <http://localhost:16686>. Stop the stack with `make down`; remove the generated scenario JSON manually if you do not want to keep it.

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

## Pool exhaustion and retry storm demos

```sh
make scenario NAME=pool_exhaustion SCENARIO_ARGS='--requests 12'
make scenario NAME=retry_storm
```

Pool exhaustion concurrently pressures a two-slot synthetic inventory pool and records measured responses and pool wait metrics. The retry storm makes exactly three downstream attempts before returning the synthetic 503. See [the scenario details](docs/demo-script.md#inventory-pool-exhaustion).

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

S0 returns a typed report with evidence references and abstains when no supported pattern matches. The dataset has 16 development and eight held-out cases; the latter are reserved for final reporting. All fixture numbers are authored synthetic inputs. Pool-exhaustion and retry-storm scenarios have been exercised in Docker; see [their measured artifacts](evals/reports/w6a/) and [evaluation design and limitations](docs/evaluation.md) for dataset boundaries and interpretation.

Run the rules benchmark on the development split with `make benchmark`. It writes per-case raw
reports plus aggregate JSON and Markdown under `evals/reports/dev-rules/`. Held-out scoring
requires the explicit `--split held_out --final-report` flags. Metrics and their interpretation
are documented in [evaluation design](docs/evaluation.md).

The S1 local LLM summary strategy uses Ollama without retrieval or diagnostic tools. After
installing and starting native Ollama with a local model (see the [Apple Silicon setup](docs/native-ollama.md)), run
`make benchmark BENCHMARK_ARGS='--strategy llm_summary --model MODEL_TAG'`, replacing
`MODEL_TAG` with the installed tag. Its raw outputs, model metadata, token counts, and latency are
saved under `evals/reports/dev-llm_summary/`.

To compare rules, LLM summary, RAG, and agent on the same development split, start Ollama with a
model and run:

```sh
make benchmark BENCHMARK_ARGS='--strategy all --model MODEL_TAG'
```

This writes per-strategy case reports and a comparison summary under
`evals/reports/dev-comparison/`. Failures, latency, and token usage are reported; the command does
not establish that an AI strategy improves on rules.

## Knowledge retrieval

The curated `knowledge/` corpus is split by Markdown section and assigned stable chunk IDs. Run
the development lexical, vector, and hybrid retrieval comparison with `make retrieval-eval`;
reports go to `evals/reports/retrieval-dev/`. For PostgreSQL full-text and pgvector, start the
stack, apply migrations, run `make knowledge-reindex`, then run
`make retrieval-eval RETRIEVAL_ARGS='--backend postgres'`. For semantic vectors, install the
optional retrieval dependency with `uv sync --frozen --extra retrieval`, then pass
`--embedding-model BAAI/bge-small-en-v1.5` to both commands. Model inference runs locally after its
first-use download. See [retrieval evaluation details](docs/evaluation.md).

## Application development

Install Python 3.12 (3.11 is also supported) and [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv.lock` pins the resolved dependency graph. `make sync` installs the locked development dependencies, and the Docker image installs locked runtime dependencies without dev tools.

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

See the [threat model](docs/threat-model.md) for trust boundaries, current controls, and security limitations. This is a local development stack, with no API authentication or authorization.

## Validation status

The current offline suite passes 160 tests on Python 3.12.14, including benchmark accounting and simulator downstream-status checks. PostgreSQL migration/persistence and row-lock behavior, Collector volume access, the deployment regression, pool exhaustion, retry storm, and investigation failure persistence were previously verified in Docker; the current status and evidence links are in [TASKS.md](TASKS.md). The rules baseline scored 8/8 on the authored synthetic held-out fixtures ([report](evals/reports/final-rules/benchmark.md)); the set has no expected-abstention cases and labels are unaudited. The offline suite and synthetic scores do not establish production usefulness. The native 4B follow-up produced valid reports but no accepted root-cause diagnoses; see the [final evaluation](docs/comparative-evaluation.md).

## Scope and next session

The comparative benchmark supports rules, LLM summary, RAG, and agent strategies on the same frozen cases and writes per-strategy plus comparison reports. The [measured 16-case dev comparison](evals/reports/dev-comparison-qwen05-bounded/comparison.md) records rules accuracy of 1.00 and 100% model-backed case failures with `qwen2.5:0.5b` (invalid structured output; three agent timeouts). This verifies report generation and failure accounting, but it does not establish model diagnostic quality. W6B is complete with a documented negative result; see the [final comparison](docs/comparative-evaluation.md) for latency, failure examples, resource observations, and follow-up limits. The [native Qwen3 4B run](evals/reports/dev-comparison-qwen3-4b-native/comparison.md) scored summary 4/16, RAG 1/16, and agent 1/16; every valid model report abstained. Native setup and reproduction commands are in the [Ollama guide](docs/native-ollama.md).
