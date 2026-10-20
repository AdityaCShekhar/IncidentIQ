# Incident and evidence API

The API persists incident metadata and collected evidence in PostgreSQL. Creation synchronously runs the bounded local telemetry collector. Evidence queries read saved rows, so they continue to work after files rotate, source telemetry becomes unavailable, or the API restarts.

## Start and migrate

```sh
make up
```

The migration service upgrades through `0004_investigations`. Existing incident rows receive empty collection metadata and zero evidence counts; their existing metadata and status are preserved. Readiness requires the current migration revision. `make migrate` applies the same upgrade explicitly.

The API and Collector run as UID 10001. A separate startup job initializes ownership of the dedicated telemetry volume, including files from earlier local stacks. That job has no network and only the `CHOWN` capability. The Collector has a writable telemetry mount; the API reads it through a read-only mount. Client requests cannot change its path. Host API development can set `TELEMETRY_DIRECTORY` to a local OTLP snapshot directory.

## Create from a scenario window

Run `make scenario NAME=slow_dependency` and allow a few seconds for asynchronous telemetry export. Generate the request from the saved fault phase:

```sh
uv run python - <<'PY' > docs/demos/incident-request.json
import json
from pathlib import Path
from incidentiq.ingestion.cli import window_from_scenario

window = window_from_scenario(
    Path("docs/demos/slow-dependency.json"), "fault", "checkout", ["inventory"]
)
request = window.model_dump(mode="json")
request.update(title="Checkout latency investigation", severity="high", scenario_id="slow_dependency")
print(json.dumps(request))
PY

curl -fsS -X POST http://localhost:8000/incidents \
  -H 'Content-Type: application/json' \
  --data-binary @docs/demos/incident-request.json
```

`POST /incidents` accepts:

| Field | Required | Constraint |
| --- | --- | --- |
| `title` | Yes | Nonblank; at most 200 characters |
| `service` | Yes | Service identifier, at most 100 characters |
| `started_at`, `ended_at` | Yes | Aware timestamps; positive window of at most 1 hour; normalized to UTC |
| `severity` | Yes | `critical`, `high`, `medium`, or `low` |
| `description` | No | At most 4,000 characters |
| `scenario_id` | No | At most 100 characters |
| `related_services` | No | At most 8 explicit service identifiers to include in collection |

Unknown fields and invalid text are rejected with HTTP 422. Related services extend the v1 request fields to make dependency collection explicit; no dependency graph is inferred. Metadata such as `scenario_id` is stored separately from normalized evidence and must not become a diagnosis feature in the evaluation harness.

The response is HTTP 201 with a UUID, `Location: /incidents/{id}`, and `X-Request-ID`. It includes incident status, UTC timestamps, saved evidence count, and a `collection` object containing collection status, limits, coverage, issues, and the snapshot record count.

HTTP 201 confirms that an incident record was created. Check its status and collection details before relying on its evidence:

| Incident status | Meaning |
| --- | --- |
| `collecting` | Creation was committed; evidence has not been committed yet |
| `ready` | At least one evidence record is saved; collection can still be partial |
| `failed` | Collection finished without usable evidence, or the collector failed |
| `created` | Reserved for existing rows or future staged ingestion |

An empty or unavailable source still produces a persisted incident with `failed` status and its collection issues. A partial bundle with usable records produces `ready` status and `collection.status=partial`. `ready` does not mean that a cause was identified or that telemetry is complete.

## Read incident and evidence

Use the actual UUID from the creation response:

```sh
curl -fsS http://localhost:8000/incidents/YOUR_INCIDENT_ID
curl -fsS 'http://localhost:8000/incidents/YOUR_INCIDENT_ID/evidence?service=inventory&source=trace&limit=20&offset=0'
```

`GET /incidents/{id}` returns saved metadata and collection status. `GET /incidents/{id}/evidence` returns a page with `incident_id`, `items`, filtered `total`, `limit`, `offset`, and `has_more`.

Evidence filters are optional:

- `service`: one exact service identifier.
- `source`: `log`, `trace`, `metric`, `deployment`, `runbook`, or `prior_incident`. The current file adapter emits the first four; structured deployment logs become deployment records. Runbooks and prior incidents await knowledge ingestion.
- `severity`: `trace`, `debug`, `info`, `warning`, `error`, or `critical`.
- `limit`: 1–100, default 50.
- `offset`: 0–1000, default 0.

Pages sort by persisted timestamp then UUID, with microsecond timestamp resolution. Original source nanoseconds remain in each payload. All filters are bound SQLAlchemy expressions and every page is scoped to its incident. The incident keeps at most 1000 evidence records. Each item includes the normalized payload, stable UUID, content hash, source URI, and raw locator.

## Persistence and failures

Creation commits `collecting` before reading files, releasing the database transaction during collection. The collector has the same five-second budget and byte/record limits as the [file CLI](evidence.md). Evidence insertion, counts, and final collection status are committed together in a second transaction. PostgreSQL locks that incident row while finalizing; a unique incident/content-hash constraint prevents duplicate observations. Replaying the same bundle through the repository keeps the same records and IDs. Previously persisted evidence is retained.

Database connection, pool, and statement operations have configured timeouts. Database outages and timeouts return HTTP 503 with safe error codes. If finalization fails after creation committed, the 503 error includes the incident UUID so it can be looked up later. No database exception strings, SQL parameters, request bodies, or collected payloads enter request logs.

Errors use this shape:

```json
{"error":{"code":"incident_not_found","message":"Incident not found","request_id":"..."}}
```

An unknown incident returns 404, malformed UUIDs/fields/filters return 422, and unexpected application failures return a safe 500. Request IDs are included in the response header and error body. A collector exception is recorded as a failed collection with the `collector_failed` issue code.

A process interruption or failed database finalization can leave an incident in `collecting`. There is no automatic recovery worker or public recollection endpoint in this slice. Inspect the saved incident; a later creation after telemetry export uses a new UUID. Repeated POST requests create distinct incidents. Authentication and public deployment are outside the local v1 setup.

## Run an investigation

`POST /incidents/{id}/investigations` accepts `{"strategy":"rules"}` or one of `llm_summary`, `rag`, and `agent` with a local Ollama `model` name. The call runs synchronously and returns a persisted investigation ID, status, structured report, model metadata, and tool-call audits. Rules needs no model. RAG adds up to five curated knowledge excerpts; the agent can make at most five allowlisted, read-only evidence and knowledge tool calls before diagnosis. Agent planning plus diagnosis is capped at three model calls, including one schema/citation repair attempt. The Ollama endpoint is fixed to loopback, and model requests have a bounded deadline.

Use `GET /investigations/{id}` to read a saved result or failure. Runs and bounded tool summaries are persisted by migration 0004. Raw telemetry is not copied into tool audit records. Local model and PostgreSQL runtime verification remains pending.

## Verification status

Offline tests exercise the actual HTTP routes, file collector, ORM queries, and database transactions using a SQLite file with foreign keys enabled. They cover persistence after source removal and app restart, filters/pages and incident isolation, duplicate collection, atomic rollback, invalid inputs, collection failures, and safe database errors. SQLite is a test dependency provided by Python, not a supported application backend.

The PostgreSQL integration check passed against a disposable database, covering the HTTP create/read/evidence path, JSONB payloads, UUIDs, timezone-aware timestamps, migration alignment, and downgrade. A separate row-lock concurrency check remains pending; the integration check does not establish lock behavior.
