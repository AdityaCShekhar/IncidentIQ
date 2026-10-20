# IncidentIQ threat model

## Scope and assumptions

This assessment covers the current single-user development stack in Docker Compose. It assumes a trusted developer workstation and Docker daemon. The stack is intended for local demonstrations with synthetic incidents; it has not been hardened for internet-facing or multi-user deployment.

## Assets

- Telemetry and evidence records, which may contain operational details or secrets.
- Incident descriptions, investigation reports, model outputs, and tool-call audit records.
- PostgreSQL credentials and persistent database contents.
- Retrieved runbooks and prior incident notes supplied to an investigation.
- Availability of the API, simulator, database, and local model runtime.

## Trust boundaries

1. **Host to Compose services:** API, database, simulator, Collector, Jaeger, and Prometheus ports are published on host loopback. Containers also communicate over the Compose network.
2. **Telemetry volume to evidence collector/API:** Collector files are treated as untrusted input. The evidence command mounts them read-only and runs without network access. The API mounts the volume read-only.
3. **Evidence and knowledge to model:** Telemetry strings and retrieved excerpts are untrusted data. The model endpoint is restricted to loopback; model output is still treated as untrusted.
4. **Model to diagnostic tools:** The agent can request only a fixed set of read-only evidence and knowledge operations. It cannot request arbitrary shell commands, SQL, network access, or production changes through this tool registry.

## Main threats and current controls

| Threat | Current controls | Remaining exposure |
| --- | --- | --- |
| Malicious or malformed telemetry exploits parsing or consumes excessive resources | Fixed filename pattern; symlink rejection; regular-file checks; allowlisted attributes; bounded files, bytes, line size, observations, records, and deadline; selected credential-like strings are masked | Sanitization is not complete secret detection. API-level rate limiting and hostile multi-tenant isolation are not implemented. |
| Prompt injection in logs, evidence, or retrieved documents changes model behavior | Prompts label these inputs as untrusted; model calls use a local loopback endpoint; outputs and evidence references are schema-validated; references must resolve to supplied evidence | Prompt instructions are not a security boundary. A model can still produce misleading diagnoses or unsafe advice. A valid evidence ID does not prove that evidence supports a claim. |
| Model attempts an unauthorized action | Tool names are fixed, arguments are schema-validated, and implemented tools only search/read the supplied bundle and curated corpus; tool and model call counts are bounded | This protects only the implemented workflow. It does not make arbitrary model output safe to execute elsewhere. |
| Network exposure reveals incidents or permits unauthenticated API use | Compose publishes service ports on host loopback; the evidence collector has no network; the application container runs as an unprivileged user | The API has no authentication or authorization and uses plain HTTP. Any deployment that exposes it beyond a trusted local machine requires a separate security design. |
| Incident data is lost or exposed through storage | PostgreSQL persists incident and evidence records; API and Collector mounts are read-only where appropriate; local defaults use synthetic data | Encryption at rest, backup/restore policy, retention/deletion controls, and tenant separation are not implemented. Local database credentials are development defaults. |
| Resource exhaustion affects local services | Collection and investigation inputs, tool calls, request windows, and model deadlines have explicit bounds; Collector has a memory limiter | The Compose stack has no production capacity management, request rate limiting, or availability guarantee. |

## Operational guidance

- Use synthetic or sanitized telemetry and do not put production credentials in the local demo.
- Keep Compose ports bound to loopback. Do not expose the API, database, telemetry endpoints, or observability UIs to an untrusted network.
- Treat diagnoses and suggested next steps as untrusted output; verify them against source evidence before acting.
- Treat local database contents and telemetry files as sensitive, and remove the Compose volumes when they are no longer needed.
- Do not describe this development configuration as production-ready or as providing authentication, tenant isolation, encryption, or autonomous remediation.

## Residual risks and out of scope

This document records the current design boundaries; it is not a penetration test or a guarantee of security. Dependency and container supply-chain review, authentication, authorization, TLS, rate limiting, audit retention, secret lifecycle, encrypted backups, and production deployment controls remain out of scope. The evaluation data is synthetic and does not validate model safety or diagnostic quality on production incidents.
