"""S0: explicit symptom-to-cause heuristics. No text instructions are executed."""

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite

from incidentiq.agent.output import Hypothesis, InvestigationReport
from incidentiq.agent.validators import validate_evidence_references
from incidentiq.ingestion.normalize import nanoseconds
from incidentiq.ingestion.schemas import EvidenceBundle, EvidenceRecord

RULES_VERSION = "rules-v1"
MIN_SLOW_MS = 200
MIN_DOWNSTREAM_MS = 150
MIN_DEPENDENCY_SHARE = 0.7
MIN_PENDING = 5
MIN_UTILIZATION = 0.9
METRIC_PROXIMITY_NS = 10_000_000_000


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not isfinite(value):
            return None
    except OverflowError:
        return None
    return value


def attrs(record):
    value = record.payload.get("attributes", {})
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Span:
    record: EvidenceRecord
    trace: str
    span: str
    parent: str | None
    start: int
    end: int
    duration_ms: float
    status: int
    kind: int


def read_span(record):
    payload = record.payload
    if record.source_type != "trace":
        return None
    try:
        start, end = int(payload["started_at_unix_nano"]), int(payload["timestamp_unix_nano"])
        duration = numeric(payload["duration_ms"])
        status = attrs(record).get("http.response.status_code")
        kind = payload.get("kind")
        trace_id, span_id = payload["trace_id"], payload["span_id"]
        if (
            duration is None
            or duration < 0
            or start > end
            or abs(duration - (end - start) / 1_000_000) > 0.001
            or isinstance(status, bool)
            or not isinstance(status, int)
            or not 100 <= status <= 599
            or isinstance(kind, bool)
            or not isinstance(kind, int)
            or kind not in (2, 3)
            or not isinstance(trace_id, str)
            or len(bytes.fromhex(trace_id)) != 16
            or not isinstance(span_id, str)
            or len(bytes.fromhex(span_id)) != 8
        ):
            return None
        return Span(
            record,
            trace_id,
            span_id,
            payload.get("parent_span_id"),
            start,
            end,
            duration,
            status,
            kind,
        )
    except (ValueError, KeyError, TypeError):
        return None


def hypothesis(cause, records, explanation, confidence=0.7):
    return Hypothesis(
        cause=cause,
        confidence=confidence,
        supporting_evidence_ids=sorted({record.id for record in records}, key=str),
        contradicting_evidence_ids=[],
        explanation=explanation,
    )


def run_rules(bundle: EvidenceBundle) -> InvestigationReport:
    """Accept telemetry alone; case IDs, labels and scenario configuration are not arguments."""
    if len(bundle.records) > bundle.limits.max_records:
        raise ValueError("Evidence exceeds the configured record budget")
    if any(
        record.service not in bundle.window.services
        or not bundle.window.started_at <= record.timestamp < bundle.window.ended_at
        for record in bundle.records
    ):
        raise ValueError("Evidence falls outside the selected incident scope")
    service = bundle.window.service
    spans = [span for record in bundle.records if (span := read_span(record)) is not None]
    roots = [span for span in spans if span.kind == 2 and span.record.service == service]
    clients = defaultdict(list)
    for span in spans:
        if span.kind == 3 and span.record.service == service:
            clients[(span.trace, span.parent)].append(span)
    children = {
        (root.trace, root.span): sorted(
            [
                child
                for child in clients[(root.trace, root.span)]
                if root.start <= child.start <= child.end <= root.end
            ],
            key=lambda span: (span.start, str(span.record.id)),
        )
        for root in roots
    }
    symptoms = [root for root in roots if root.duration_ms >= MIN_SLOW_MS or root.status >= 500]
    candidates = []

    # A single slow dependency call accounts for most of each slow upstream request.
    slow = []
    slow_requests = set()
    for root in roots:
        calls = children[(root.trace, root.span)]
        if root.duration_ms < MIN_SLOW_MS or len(calls) != 1:
            continue
        call = calls[0]
        if (
            isinstance(attrs(call.record).get("peer.service"), str)
            and call.status == 200
            and call.duration_ms >= MIN_DOWNSTREAM_MS
            and call.duration_ms >= root.duration_ms * MIN_DEPENDENCY_SHARE
        ):
            slow.extend([root.record, call.record])
            slow_requests.add(root.trace)
    if len(slow_requests) >= 2:
        candidates.append(
            hypothesis(
                "slow_dependency",
                slow,
                "In at least two traces, one successful dependency call occupies most "
                "of a slow service request. "
                "This pattern is consistent with a slow downstream dependency.",
            )
        )

    # Explicit retry indices distinguish retries from ordinary parallel dependency fan-out.
    retries, retry_requests = [], set()
    for root in roots:
        if root.duration_ms < MIN_SLOW_MS:
            continue
        operations = defaultdict(list)
        for call in children[(root.trace, root.span)]:
            peer = attrs(call.record).get("peer.service")
            name = call.record.payload.get("name")
            if isinstance(peer, str) and isinstance(name, str):
                operations[(peer, name)].append(call)
        for calls in operations.values():
            attempts = [attrs(call.record).get("incidentiq.retry_attempt") for call in calls]
            if (
                3 <= len(calls) <= 6
                and all(
                    isinstance(attempt, int) and not isinstance(attempt, bool)
                    for attempt in attempts
                )
                and attempts == list(range(len(calls)))
                and sum(call.status >= 500 for call in calls) >= 2
            ):
                retries.extend([root.record, *(call.record for call in calls)])
                retry_requests.add(root.trace)
    if len(retry_requests) >= 2:
        candidates.append(
            hypothesis(
                "retry_storm",
                retries,
                "At least two slow requests contain three or more explicit attempts "
                "on the same dependency operation, including repeated failures. "
                "This indicates retry amplification within those requests.",
            )
        )

    # Gauge samples only: cumulative totals never become incident-window saturation features.
    pools = defaultdict(lambda: defaultdict(list))
    for record in bundle.records:
        payload = record.payload
        if (
            record.service != service
            or record.source_type != "metric"
            or payload.get("kind") != "gauge"
        ):
            continue
        name = payload.get("name")
        pool = attrs(record).get("incidentiq.pool.name")
        value = numeric(payload.get("value"))
        if isinstance(pool, str) and value is not None:
            if name == "incidentiq.db.pool.waiting" and value >= MIN_PENDING:
                pools[pool]["waiting"].append(record)
            if name == "incidentiq.db.pool.utilization" and MIN_UTILIZATION <= value <= 1:
                pools[pool]["utilization"].append(record)
    pool_support = []
    for signals in pools.values():
        waiting = sorted(signals["waiting"], key=lambda record: (record.timestamp, str(record.id)))
        utilization = sorted(
            signals["utilization"], key=lambda record: (record.timestamp, str(record.id))
        )
        for root in symptoms:
            nearby_wait = [
                record
                for record in waiting
                if abs(root.end - nanoseconds(record.timestamp)) <= METRIC_PROXIMITY_NS
            ]
            nearby_util = [
                record
                for record in utilization
                if abs(root.end - nanoseconds(record.timestamp)) <= METRIC_PROXIMITY_NS
            ]
            iw = iu = 0
            while iw < len(nearby_wait) and iu < len(nearby_util):
                at_wait = nanoseconds(nearby_wait[iw].timestamp)
                at_util = nanoseconds(nearby_util[iu].timestamp)
                if abs(at_wait - at_util) <= METRIC_PROXIMITY_NS:
                    pool_support.extend([nearby_wait[iw], nearby_util[iu], root.record])
                    break
                if at_wait < at_util:
                    iw += 1
                else:
                    iu += 1
    if pool_support:
        candidates.append(
            hypothesis(
                "database_pool_exhaustion",
                pool_support,
                "The same pool has waiting acquisitions and near-full utilization "
                "alongside slow or failing "
                "service requests. This pattern is consistent with connection-pool exhaustion.",
            )
        )

    # Temporal association, matching revision, and healthy dependencies; no cause is read from text.
    deployments = [
        record
        for record in bundle.records
        if record.service == service
        and record.source_type == "deployment"
        and record.payload.get("state") == "succeeded"
    ]
    deploy_support = []
    for event in deployments:
        revision = event.payload.get("revision")
        if not isinstance(revision, str) or revision == event.payload.get("previous_revision"):
            continue
        matches = []
        traces = set()
        for root in roots:
            calls = children[(root.trace, root.span)]
            if (
                root.status >= 500
                and nanoseconds(event.timestamp) <= root.start
                and attrs(root.record).get("deployment.revision") == revision
                and calls
                and all(call.status == 200 for call in calls)
            ):
                matches.extend([root.record, *(call.record for call in calls)])
                traces.add(root.trace)
        if len(traces) >= 2:
            deploy_support.extend([event, *matches])
    if deploy_support:
        candidates.append(
            hypothesis(
                "deployment_regression",
                deploy_support,
                "A deployment precedes at least two failing requests on the matching revision "
                "while their dependency calls succeed. A deployment regression is a candidate; "
                "temporal association does not prove causation.",
            )
        )

    missing = []
    if bundle.status != "collected":
        missing.append(
            "Telemetry collection is incomplete; missing observations cannot rule out alternatives."
        )
    if not candidates:
        report = InvestigationReport(
            incident_summary=f"Evidence review for service {service} in the requested UTC window.",
            hypotheses=[
                hypothesis(
                    "unknown",
                    [],
                    "The available observations do not establish a supported cause pattern.",
                    0,
                )
            ],
            missing_evidence=[
                *missing,
                "Obtain correlated dependency spans, deployment/revision history, "
                "pool gauges, and retry-attempt observations.",
            ],
            recommended_next_steps=[
                "Collect the missing diagnostic signals for the same service and time window."
            ],
            disposition="insufficient_evidence",
        )
    else:
        candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.cause))
        if len(candidates) > 1:
            missing.append(
                "Multiple cause patterns match; collect discriminating evidence "
                "before choosing one."
            )
        next_steps = {
            "slow_dependency": (
                "Inspect correlated dependency latency and saturation in the same window."
            ),
            "database_pool_exhaustion": (
                "Inspect pool acquisition waits and long-running database operations."
            ),
            "deployment_regression": (
                "Compare the deployed code and response contract with the previous revision."
            ),
            "retry_storm": "Inspect retry limits, backoff, and attempts per upstream request.",
        }
        report = InvestigationReport(
            incident_summary=f"Evidence review for service {service} in the requested UTC window.",
            hypotheses=candidates[:3],
            missing_evidence=missing,
            recommended_next_steps=[next_steps[candidate.cause] for candidate in candidates[:3]],
            disposition="likely_cause_found" if len(candidates) == 1 else "inconclusive",
        )
    return validate_evidence_references(report, bundle)
