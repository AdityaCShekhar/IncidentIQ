# Collecting incident evidence

The evidence collector reads the local Collector's OTLP JSON files and returns a typed bundle for a service and UTC time window. It does not diagnose an incident. The [incident API](api.md) runs the same collector during incident creation and persists its output in PostgreSQL.

## Collect a simulator window

```sh
make simulator-up
make scenario NAME=slow_dependency
```

Allow a few seconds for asynchronous telemetry export, then collect the fault phase:

```sh
make evidence > docs/demos/slow-dependency-evidence.json
```

For the second fault, run `make scenario NAME=deployment_regression`, wait for export, then `make evidence NAME=deployment_regression > docs/demos/deployment-regression-evidence.json`. Its fault window includes the deployment activation before checkout errors. `--phase recovery` is also available when calling the collector CLI directly with that scenario artifact.

The `evidence` Compose service reads the telemetry volume and the scenario artifact through read-only mounts. Its network is disabled, root filesystem is read-only, and all capabilities are dropped. It uses the Collector's UID to read files regardless of their default permissions. Image build messages and collection status go to stderr; the bundle alone goes to stdout. The task requires the scenario artifact to exist before mounting its directory.

The scenario artifact supplies only the selected phase's start and end times. Injected delay configuration and phase labels are excluded from the bundle. `checkout` is the affected service and `inventory` is explicitly included as a related service. The adapter does not infer a dependency graph.

To collect local files directly, use Python and uv:

```sh
uv run python scripts/collect_evidence.py \
  --directory ./sample-telemetry \
  --service checkout --related-service inventory \
  --started-at 2026-10-19T00:00:00Z \
  --ended-at 2026-10-19T00:05:00Z \
  --output ./evidence.json
```

These timestamps are an input example; choose a window containing your telemetry. `--scenario-file docs/demos/slow-dependency.json --phase fault` can replace explicit timestamps. File output is replaced atomically, and the CLI rejects output paths that would replace its input telemetry or scenario artifact.

## Bundle and source identities

Each record includes a UUID, incident UUID, source type, service, UTC timestamp, severity, summary, bounded diagnostic payload, SHA-256 content hash, stable `otel://` source URI, and raw file/line/JSON-pointer locator. The source URI identifies a trace/span, log observation, deployment event, or metric point; it is not a network URL to fetch.

Structured simulator deployment logs are emitted as `source_type=deployment`. They must have `event.name=deployment`, body `deployment_changed`, a UUID event ID, succeeded state, and distinct known previous/current revisions. Their locator is `otel://checkout/deployments/EVENT_UUID`; payloads retain revision attributes, event time, and run correlation. Malformed structured events produce `invalid_observation`; ordinary log text mentioning deployments stays log evidence. This classification does not authenticate the source or prove that a deployment caused an error.

Coverage describes the three physical OTLP file streams: trace, log, and metric. Deployment records are derived from the log stream and counted in its `records_collected` and duplicate totals. There is no separate deployment file and no additional required stream.

Without `--incident-id`, the incident UUID is derived deterministically from the normalized window and selected services. Pass a persisted incident UUID when integrating with the HTTP API. Record UUIDs derive from the incident UUID and normalized content hash. The same observation can therefore belong to separate incidents without a primary-key collision, while its content hash remains the same.

File names, line positions, attribute ordering, and duplicate exports do not change record IDs. Hex and protobuf JSON base64 trace/span identifiers normalize to lowercase hex; enum names and numeric forms are supported. Duplicate observations are counted and emitted once. Records are sorted by original nanosecond timestamp and UUID. JSON timestamps use UTC microseconds, and payloads retain the source nanoseconds.

Raw locators describe the file snapshot actually read and may change after rotation. Repeated collection of unchanged files and configuration produces identical bundle JSON; collecting a live, growing volume may produce additional records. A source URI is retained with normalized evidence even when file rotation later removes the raw observation.

## Window and metric semantics

Windows must have timezone-aware timestamps, positive duration, and a maximum length of one hour. They are normalized to UTC and use `[started_at, ended_at)`. Traces are selected by span completion time; logs by event time (observed time when event time is absent or zero); metrics by point timestamp. Unfinished spans and spans ending outside the window are excluded. Include a suitable margin when investigating requests that straddle a boundary.

Metric records retain their original name, unit, kind, attributes, aggregation temporality, interval, and values. Supported kinds are gauge, sum, and explicit histogram. A cumulative histogram may include requests from before the incident. Its count and sum are stored as a snapshot of that full interval, and must not be presented as counts or averages for the incident window. Computing trustworthy interval deltas is future work.

Only allowlisted scalar diagnostic attributes are retained. Span names and log messages are bounded; common `password=`, `token=`, `api_key=`, and `authorization=` patterns are masked. This is not a general secret detector. Use synthetic telemetry, and treat all retained text as untrusted data in later model prompts.

## Bounds and incomplete data

Default collection limits:

| Bound | Default |
| --- | --- |
| Window | At most 1 hour |
| Related services | At most 8 |
| Directory entries inspected | At most 256 |
| Matching files | At most 16 |
| Total scanned bytes | At most 32 MiB |
| JSON line size | At most 1 MiB |
| Lines scanned | At most 100,000 |
| Observations examined | At most 25,000 |
| Returned unique records | At most 1,000 |
| Collection time budget | 5 seconds, checked between reads and observations |

The adapter reads active and rotated `traces*.jsonl`, `logs*.jsonl`, and `metrics*.jsonl` files matching the Collector's filename pattern. Files are scanned in trace/log/metric order, with active files first. Each opened file is read up to its starting size. File symlinks and nonregular files are refused. If the return limit is reached, the earliest matching records within the scanned input are retained deterministically.

Statuses and direct CLI exit codes:

| Status | Meaning | Exit code |
| --- | --- | --- |
| `collected` | Matching records collected without reported read/parse/budget issues | 0 |
| `partial` | Some data is missing, invalid, unreadable, or excluded by a limit | 2 |
| `empty` | Files scanned successfully with no matching records | 3 |
| `unavailable` | No source files could be opened and scanned | 3 |
| Invalid invocation | Invalid window, arguments, artifact, or output destination | 1 |

Every bundle includes per-source coverage counts and issue categories. An empty file or an unrelated service may contribute no records even when status is `collected`; inspect record coverage for each required service and signal. A successful local scan does not prove upstream telemetry completeness. Asynchronous exports, retention, dropped queues, rotation during discovery, and record limits can hide evidence. Retry after export delay or adjust the window, and preserve explicit uncertainty when data is absent.

Argument parser usage errors use the standard exit code 2 and produce no bundle. `make` reports a failed recipe for nonzero exit codes; partial/unavailable bundle JSON is still written to stdout. Never interpret a nonzero invocation as a successful full collection just because its output is valid JSON.

## Verification

Unit checks cover time boundaries, stable IDs and deduplication across rotation, source locators, cumulative metric scope, malformed input, scan limits, deadlines, symlinks, basic masking, CLI statuses, and protected output paths. The end-to-end in-process test calls the actual checkout/inventory ASGI services, encodes their SDK spans/logs/metrics to protobuf JSON, collects those files, and checks the inventory delay appears in normalized trace evidence with HTTP 200 and stable replay IDs.

Live collection from the Docker volume remains pending because this workspace cannot access the Docker daemon. No live demo or diagnosis benchmark results are claimed. Format handling follows the pinned Collector file exporter and [OTLP JSON encoding](https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding).
