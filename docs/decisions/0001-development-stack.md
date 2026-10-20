# Development stack

Status: accepted; updated for persisted incident collection.

## Decision

Use one Python application image for the incident API and the two synthetic services. The simulator lives in `app/incidentiq/simulator` so it shares packaging and logging without requiring another build system. PostgreSQL 17 stores application records; Alembic owns schema changes. Add pgvector when retrieval begins.

Use manual OpenTelemetry instrumentation for the three spans in `checkout → inventory`. The small explicit span set makes request ancestry and diagnostic attributes easy to inspect. Each service owns its SDK providers; application factories accept test transports and in-memory exporters without replacing global providers.

Route all three signals through Collector Contrib 0.148.0. It forwards traces to Jaeger 2.22.0, exposes duration metrics for Prometheus 3.13.4, and stores OTLP JSON lines on a named volume for bounded evidence collection. The Python SDK and HTTP exporters are pinned together to 1.44.0 because log APIs and exported data formats require version-aware handling.

## Consequences

The simulator runs independently of the database and incident API. Fault settings are per request, which avoids shared mutable fault toggles. Synthetic request schemas restrict delays and reject unknown arguments; the dependency URL comes from service configuration.

All published ports bind to loopback. Inventory and the Collector's Prometheus scrape endpoint remain internal. The Collector, API, simulator, and evidence CLI share unprivileged UID 10001. A separate startup job assigns ownership of the dedicated telemetry volume with only the `CHOWN` capability and no network. This includes existing files from earlier local stacks. The Collector writes the volume with all capabilities dropped and a read-only root filesystem; the API and evidence CLI mount telemetry read-only. The evidence CLI also has no network.

Jaeger provides a temporary inspection view; the Collector volume is the persistent trace/log source. File output is intended for the local benchmark scale, with rotation and explicit handling of missing or expired telemetry. [Collector documentation](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.148.0/exporter/fileexporter/README.md) describes the format and its stability limits.

Full dependency locking, container startup, backend configuration validation, and live telemetry inspection remain pending. The image tags and Python SDK pins do not replace a complete dependency lock or a clean Docker verification.
