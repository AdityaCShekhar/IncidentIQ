# Incident simulator demos

The simulator supports four repeatable faults over real HTTP requests: a slow dependency, bounded inventory pool exhaustion, deployment regression, and a three-attempt retry storm. It records observed responses and emits traces, logs, and metrics for the incident evidence workflow.

## Start the services

Install Docker with Compose and Make. Install Python 3.12 (3.11 also supported) and [uv](https://docs.astral.sh/uv/getting-started/installation/) for the scenario command.

```sh
make up
make telemetry-config
make scenario NAME=slow_dependency
```

To work on the simulator without starting the database and incident API, use `make simulator-up` instead of `make up`. Both start the telemetry dependencies. The default URLs are:

| Component | URL | Purpose |
| --- | --- | --- |
| Incident API | http://localhost:8000/docs | Session 1 API documentation |
| Checkout simulator | http://localhost:8001/docs | Synthetic requests |
| Jaeger | http://localhost:16686 | Correlated request traces |
| Prometheus | http://localhost:9090 | Request duration histogram |
| Collector health | http://localhost:13133 | Collector process health |

Ports can be changed in `.env`. `make scenario` defaults to port 8001; pass `SCENARIO_ARGS='--base-url http://127.0.0.1:YOUR_PORT'` if you change it.

`make telemetry-config` invokes the Collector's configuration validator and Prometheus's `promtool`. Successful Compose parsing alone does not validate their configuration schemas or establish that telemetry was delivered.

## Run the fault

The default scenario sends five sequential baseline requests with a 20 ms inventory delay, then five fault requests with a 500 ms delay. Each phase gets its own run ID, and each request gets a request ID and trace ID. The script saves measured responses, UTC time windows, and medians to `docs/demos/slow-dependency.json`.

```sh
make scenario NAME=slow_dependency SCENARIO_ARGS='--requests 10 --fault-ms 1000'
```

Request count is bounded to 1–20 per phase, delay to 0–2000 ms, and each client request has a five-second HTTP operation timeout. Inventory calls use a three-second HTTP operation timeout by default. The scenario rejects invalid configurations before sending requests. Exit code 1 means request/response failure; exit code 2 means the measured fault median did not increase (the measurement file is still saved).

The injected delay is scoped to each request. A later request with the baseline delay returns to normal behavior without changing shared service state. Delay configuration and phase labels are saved in the demo artifact, and are excluded from telemetry attributes used for diagnosis. Run IDs are correlation identifiers rather than fault labels.

After a few seconds for asynchronous export, run `make evidence > docs/demos/slow-dependency-evidence.json` to collect the fault time window. See [evidence collection](evidence.md) for stable IDs, source locators, limits, and explicit handling of missing data. The evidence collector reads only the phase timestamps from the scenario artifact.

To persist that window as an incident and query saved evidence, follow [the incident API walkthrough](api.md). HTTP queries read the saved snapshot, so source file rotation does not remove its records.

## Inspect a trace and its logs

Allow a few seconds for asynchronous export, then open Jaeger. Search for service `checkout`, or open a trace ID printed by the scenario at `http://localhost:16686/trace/TRACE_ID`.

The expected chain is:

```text
checkout: POST /checkout                 (server span)
  └─ checkout: POST inventory /reserve   (client span)
       └─ inventory: POST /reserve       (server span)
```

For a fault request, the inventory server span should account for most of the checkout duration while the HTTP status remains 200. Compare it with a baseline trace before drawing a conclusion.

```sh
docker compose logs --since=5m checkout inventory
```

Match `trace_id`, `request_id`, and `run_id` across the two services. The logs include service, UTC timestamp, status, and observed duration. They omit request bodies, injected delay settings, raw dependency exceptions, and arbitrary headers.

## Inspect latency metrics

After running traffic, wait at least a few seconds for metric export and scraping. Open Prometheus and inspect:

```promql
incidentiq_request_duration_seconds_count
```

To compare mean request durations by service over recent traffic:

```promql
sum by (service_name) (rate(incidentiq_request_duration_seconds_sum[1m]))
/
sum by (service_name) (rate(incidentiq_request_duration_seconds_count[1m]))
```

For a clearer graph, run additional scenarios while viewing the chart. A short run may not produce enough scrape samples for a rate; the scenario artifact provides the per-phase measured client medians. Compare inventory and checkout latency and status together. Request, run, and trace IDs are deliberately excluded from metric labels.

## Deployment regression

```sh
make scenario NAME=deployment_regression
```

The command runs three phases using one random run ID, with five requests per phase by default:

| Phase | Checkout revision | Expected checkout status | Expected inventory status |
| --- | --- | --- | --- |
| baseline | `checkout-v1` | 200 | 200 |
| fault | `checkout-v2` | 500, `response_contract_error` | 200 |
| recovery | `checkout-v1` | 200 | 200 |

These are authored expectations; the command validates actual responses and records observed error rates and client latencies. Results go to `docs/demos/deployment-regression.json`. `--requests` remains bounded to 1–20 per phase. `--baseline-ms` and `--fault-ms` apply only to the slow-dependency scenario; this scenario keeps inventory delay at 20 ms throughout.

Docker verification on 2026-10-20 measured five requests per phase: baseline median 31.955 ms with 0% errors, fault median 32.375 ms with 100% errors, and recovery median 33.002 ms with 0% errors. The fault-window collector returned 27 records and no issues. The persisted incident `5c861a28-f478-4532-94f5-337c3799638b` contains five failing checkout server spans (`response_contract_error`), five successful inventory server spans, and the deployment change event. The measured phase results are saved in the artifact above.

Revision v2 uses a faulty response formatter that expects `reservation_id`, absent from inventory's current response contract. Its inventory client span succeeds, then the checkout server span fails. Compare the correlated spans, `error.type`, and `deployment.revision`; a checkout error alone is insufficient to attribute the cause to a deployment.

The checkout simulator's `POST /simulator/deployments` control accepts exactly a UUID `run_id` and revision `checkout-v1` or `checkout-v2`. It emits a structured `deployment_changed` event when the revision changes. Repeating an active v2 activation returns the same event without another log. Other run IDs use v1. The inventory service does not expose this route. This control belongs to the fault simulator and is never a diagnostic agent tool.

The runner rolls back before measuring recovery. If activation or a fault request fails, it attempts rollback in `finally`, including when the activation response is lost. A failed rollback makes the scenario fail clearly; abrupt process termination can prevent cleanup. At most 64 v2 overrides can exist, each for at most ten minutes from initial activation. Rollback frees its slot; repeated activation does not extend expiry. Overrides are process-local and vanish on restart. Expiry/restart resets are safeguards and emit no deployment event; use explicit rollback to demonstrate recovery. The simulator is intended for one process, as configured by Compose.

Fault and recovery windows start **before** their deployment controls, so each includes the associated change event. After export delay:

```sh
make evidence NAME=deployment_regression > docs/demos/deployment-regression-evidence.json
```

For the [API request example](api.md#create-from-a-scenario-window), substitute `docs/demos/deployment-regression.json`, title `Checkout deployment investigation`, and `scenario_id="deployment_regression"`. Query saved events with `/incidents/YOUR_INCIDENT_ID/evidence?source=deployment`. Baseline and recovery observations require their own windows, or a window spanning the whole scenario. Labels live separately in [the authored definition](../simulator/scenarios/deployment_regression.json) and never enter telemetry bundles.

Inspect the checkout deployment log before its first v2 error and the v1 rollback before recovery. Checkout duration histogram dimensions now include the two-valued `deployment.revision`, alongside route and response status; run/request/trace IDs remain excluded. A short fault phase may fall between metric exports. Traces and event logs provide per-request/change evidence; cumulative metric snapshots must retain their full interval semantics.

## Inventory pool exhaustion

```sh
make scenario NAME=pool_exhaustion SCENARIO_ARGS='--requests 12'
```

The scenario first sends baseline traffic, then concurrently sends up to 20 requests. In the fault phase, the inventory simulator has two synthetic database slots; each holds a slot for 250 ms while acquisition waits only 20 ms. Requests that cannot acquire a slot return HTTP 503. The inventory emits `incidentiq.database.pool.wait` observations for successful and timed-out acquisition. The JSON artifact saves every observed response and whether any exhaustion occurred. This is a deterministic pool pressure simulation, not a real database benchmark.

After export, collect the fault window with `make evidence NAME=pool_exhaustion > docs/demos/pool-exhaustion-evidence.json`.

## Retry storm

```sh
make scenario NAME=retry_storm
```

The checkout simulator makes three downstream attempts for one request while inventory returns synthetic 503 responses. A retry-attempt span attribute and response header expose the observed count; the artifact records the count and final status. This isolated simulator fault does not alter normal requests or shared deployment state.

The artifact includes baseline and fault windows, so collect its evidence with `make evidence NAME=retry_storm > docs/demos/retry-storm-evidence.json`.

Docker verification on 2026-10-20 measured pool exhaustion with four baseline HTTP 200s and twelve concurrent fault responses: ten HTTP 503 and two HTTP 200. The pool fault window collected 67 records with no issues. Retry-storm verification measured five baseline HTTP 200s and five fault HTTP 503s; each fault request made exactly three downstream attempts. Its fault window collected 62 records with no issues, including 15 inventory retry spans (five each for attempt indices 0, 1, and 2). See the [pool-exhaustion run and evidence](../evals/reports/w6a/pool-exhaustion.json) and [retry-storm run and evidence](../evals/reports/w6a/retry-storm.json); corresponding evidence bundles are in the same directory. The duration metrics are cumulative snapshots and retain their full interval semantics.

## Storage and limitations

The Collector writes OTLP JSON lines to `/telemetry/traces.jsonl`, `/telemetry/logs.jsonl`, and `/telemetry/metrics.jsonl` in the Compose `telemetry_data` volume. Each signal rotates at 10 MB with three backups and a one-day backup age limit. Active files remain until replaced or the volume is removed. This is a bounded local file backend for the next evidence collection slice, with format defined by the pinned Collector version. See the [file exporter documentation](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.148.0/exporter/fileexporter/README.md).

Prometheus uses persistent storage with a 24-hour/256 MB retention target. Jaeger uses its default in-memory storage, so its traces disappear on restart; Collector trace files persist. [Jaeger's default configuration](https://www.jaegertracing.io/docs/2.22/getting-started/) documents this storage behavior.

Collector process health does not prove that all export pipelines are healthy. Verify a trace in Jaeger, nonempty metric samples in Prometheus, and matching service logs. Exporter queues are finite and can lose telemetry during outages; the next collector must treat missing data explicitly.

The Docker slow-dependency run was verified on 2026-10-20. Collector and Prometheus configuration validators passed; the measured checkout median increased from 30.145 ms at baseline to 511.654 ms with the injected inventory delay. The read-only evidence collector returned 30 records with no issue categories, including traces and logs from both services and cumulative request-duration metrics. A correlated trace was available from Jaeger, and Prometheus returned request-duration samples. The measured HTTP scenario is saved at `docs/demos/slow-dependency.json`.
