# Resume summary

## Project entry

**IncidentIQ — Evidence-Grounded Incident Investigation** | Python, FastAPI, PostgreSQL/pgvector, Docker Compose, OpenTelemetry, Prometheus, Jaeger

- Built a Dockerized incident investigation backend that normalizes traces, logs, metrics, and deployment events into bounded evidence records, then persists incidents, evidence, investigations, and read-only tool audits in PostgreSQL.
- Implemented four repeatable fault scenarios and verified the telemetry flow in Docker. Pool-exhaustion evidence captured 67 records while 10/12 concurrent fault requests returned HTTP 503; retry-storm evidence captured 62 records while all 5 fault requests returned HTTP 503 after three downstream attempts.
- Implemented lexical, hashed-vector, and BGE retrieval with memory and PostgreSQL backends; compared results across 16 authored development cases and 13 curated chunks. BGE vector Recall@1 was 0.1406 versus 0.0781 for hashed features, while hybrid MRR was 0.5469 versus 0.5677, showing mixed results on this synthetic corpus.
- Added deterministic, LLM-summary, RAG, and bounded read-only agent strategies with structured outputs, evidence-reference validation, and per-case benchmark reports. The rules baseline achieved 1.00 exact top-cause accuracy on the 16-case development split and the separate eight-case held-out split; both use authored synthetic fixtures, the held-out set has no incomplete cases, and its labels remain unaudited. The 0.5B model produced no valid reports; native Qwen3 4B produced valid reports but all abstained, with summary/RAG/agent scoring 4/16, 1/16, and 1/16 on development cases. Neither configuration improved on rules.

## Shorter version

Built a Dockerized incident-investigation backend with FastAPI, PostgreSQL, and OpenTelemetry, including bounded evidence persistence, four repeatable fault scenarios, hybrid runbook retrieval, and read-only investigation tools. Verified live failure telemetry and benchmarked a deterministic baseline on authored synthetic cases; model-backed diagnosis quality remains unvalidated.

## Scope to explain in interviews

- Benchmark labels and metrics are from repository-authored synthetic fixtures, not production incidents; the independent-label audit and held-out model-strategy report remain outstanding.
- Tested model-backed strategies failed validation, timed out, or over-abstained; the native 4B follow-up had no accepted root-cause diagnoses. Do not claim AI diagnosis improves on the deterministic baseline.
- This Compose setup is for local demonstration and has no API authentication, tenant isolation, or production deployment hardening. See the [threat model](threat-model.md).
