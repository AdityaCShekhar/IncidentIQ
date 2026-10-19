import base64
import hashlib
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import TypeAdapter

from incidentiq.ingestion.schemas import EvidenceRecord, EvidenceWindow, ServiceName, SourceType

SERVICE = TypeAdapter(ServiceName)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SAFE_ATTRIBUTES = {
    "service.name",
    "http.route",
    "http.request.method",
    "http.response.status_code",
    "peer.service",
    "error.type",
    "request_id",
    "run_id",
    "incidentiq.run_id",
    "status_code",
    "latency_ms",
    "operation",
    "event.name",
    "deployment.id",
    "deployment.revision",
    "deployment.previous_revision",
    "deployment.state",
    "incidentiq.retry_attempt",
    "incidentiq.pool.name",
}
SECRETS = re.compile(
    r"(?i)\b(password|api[_-]?key|token|authorization)\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+"
)


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def nanoseconds(value: datetime) -> int:
    delta = value - EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def timestamp(value: int) -> datetime:
    seconds, nanos = divmod(value, 1_000_000_000)
    return EPOCH + timedelta(seconds=seconds, microseconds=nanos // 1000)


def uint(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Expected an unsigned integer")
    if isinstance(value, str) and (not value.isascii() or not value.isdigit()):
        raise ValueError("Invalid integer encoding")
    result = int(value)
    if not 0 <= result <= (1 << 64) - 1:
        raise ValueError("Integer outside uint64")
    return result


def number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Expected a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite number")
    return result


def text(value, limit=512) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text")
    if "\x00" in value[:limit]:
        raise ValueError("NUL is not valid PostgreSQL JSON text")
    # Preserve untrusted diagnostic text as data, with bounded size and basic credential masking.
    result = SECRETS.sub(lambda match: f"{match[1]}=[REDACTED]", value[:limit])
    result.encode("utf-8")  # Reject lone surrogate escapes before serializing a bundle.
    return result


def identifier(value, size: int, *, optional=False) -> str | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Expected trace/span identifier")
    if len(value) == size * 2:
        decoded = bytes.fromhex(value)
    else:
        decoded = base64.b64decode(value, validate=True)
    if len(decoded) != size or not any(decoded):
        raise ValueError("Invalid trace/span identifier")
    return decoded.hex()


def objects(value) -> list:
    if not isinstance(value, list):
        raise ValueError("Expected an array")
    return value


def attributes(value, allowed=SAFE_ATTRIBUTES) -> dict:
    result = {}
    for item in objects(value):
        key = item["key"]
        if key not in allowed:
            continue
        if key in result:
            raise ValueError("Duplicate diagnostic attribute")
        val = item["value"]
        if "stringValue" in val:
            result[key] = text(val["stringValue"])
        elif "intValue" in val:
            result[key] = uint(val["intValue"])
        elif "doubleValue" in val:
            result[key] = number(val["doubleValue"])
        elif "boolValue" in val and isinstance(val["boolValue"], bool):
            result[key] = val["boolValue"]
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class Observation:
    source_type: SourceType
    service: str
    value: dict
    pointer: str
    metric: dict | None = None
    metric_kind: str | None = None


def observations(document: dict, source: SourceType) -> Iterator[Observation]:
    root, scopes, items = {
        "trace": ("resourceSpans", "scopeSpans", "spans"),
        "log": ("resourceLogs", "scopeLogs", "logRecords"),
        "metric": ("resourceMetrics", "scopeMetrics", "metrics"),
    }[source]
    for ri, resource in enumerate(objects(document[root])):
        service = attributes(
            resource.get("resource", {}).get("attributes", []), {"service.name"}
        ).get("service.name")
        for si, scope in enumerate(objects(resource.get(scopes, []))):
            for oi, item in enumerate(objects(scope.get(items, []))):
                pointer = f"/{root}/{ri}/{scopes}/{si}/{items}/{oi}"
                if source != "metric":
                    yield Observation(source, service, item, pointer)
                    continue
                kind = next((key for key in ("histogram", "gauge", "sum") if key in item), None)
                if kind is None:
                    yield Observation(source, service, {}, pointer, item, None)
                    continue
                for pi, point in enumerate(objects(item[kind].get("dataPoints", []))):
                    yield Observation(
                        source, service, point, f"{pointer}/{kind}/dataPoints/{pi}", item, kind
                    )


def metric_payload(observation: Observation, value: dict, attrs: dict, at: int) -> dict:
    metric, kind = observation.metric, observation.metric_kind
    if kind is None:
        raise ValueError("Unsupported metric kind")
    start = uint(value.get("startTimeUnixNano", 0))
    if start > at:
        raise ValueError("Reversed metric interval")
    payload = {
        "name": text(metric["name"], 160),
        "unit": text(metric.get("unit", ""), 32),
        "kind": kind,
        "attributes": attrs,
        "timestamp_unix_nano": str(at),
        "interval_started_at": timestamp(start).isoformat() if start else None,
        "interval_started_at_unix_nano": str(start) if start else None,
        "interval_ended_at": timestamp(at).isoformat(),
    }
    if kind != "gauge":
        temporality = metric[kind].get("aggregationTemporality", 0)
        temporalities = {
            1: "delta",
            2: "cumulative",
            "AGGREGATION_TEMPORALITY_DELTA": "delta",
            "AGGREGATION_TEMPORALITY_CUMULATIVE": "cumulative",
        }
        if isinstance(temporality, bool) or temporality not in temporalities:
            raise ValueError("Unknown aggregation temporality")
        payload["aggregation_temporality"] = temporalities[temporality]
    if kind == "histogram":
        bounds = objects(value.get("explicitBounds", []))
        buckets = objects(value.get("bucketCounts", []))
        if len(bounds) > 128 or len(buckets) != len(bounds) + 1:
            raise ValueError("Invalid histogram size")
        bounds, buckets = [number(bound) for bound in bounds], [uint(count) for count in buckets]
        if any(a >= b for a, b in zip(bounds, bounds[1:], strict=False)):
            raise ValueError("Unsorted histogram boundaries")
        count = uint(value.get("count", 0))
        if sum(buckets) != count:
            raise ValueError("Histogram count mismatch")
        payload.update(count=count, bounds=bounds, bucket_counts=buckets)
        for key in ("sum", "min", "max"):
            if key in value:
                payload[key] = number(value[key])
    else:
        if "asInt" in value:
            raw = value["asInt"]
            if isinstance(raw, bool) or not isinstance(raw, (str, int)):
                raise ValueError("Invalid metric integer")
            integer = int(raw)
            if not -(1 << 63) <= integer < (1 << 63):
                raise ValueError("Metric integer outside int64")
            payload["value"] = integer
        else:
            payload["value"] = number(value["asDouble"])
    return payload


def normalize(
    observation: Observation, window: EvidenceWindow, incident_id: UUID, raw_locator: str
) -> EvidenceRecord | None:
    service = SERVICE.validate_python(observation.service)
    if service not in window.services:
        return None
    value = observation.value
    if not isinstance(value, dict):
        raise ValueError("Expected observation object")
    source = observation.source_type
    at = uint(value.get("endTimeUnixNano") if source == "trace" else value.get("timeUnixNano", 0))
    if source == "log" and at == 0:
        at = uint(value.get("observedTimeUnixNano"))
    if not nanoseconds(window.started_at) <= at < nanoseconds(window.ended_at):
        return None
    attrs = attributes(value.get("attributes", []))
    severity = "info"
    if source == "trace":
        start = uint(value["startTimeUnixNano"])
        if start > at:
            raise ValueError("Reversed span interval")
        trace_id = identifier(value.get("traceId"), 16)
        span_id = identifier(value.get("spanId"), 8)
        kind = value.get("kind", 0)
        kinds = {
            "SPAN_KIND_UNSPECIFIED": 0,
            "SPAN_KIND_INTERNAL": 1,
            "SPAN_KIND_SERVER": 2,
            "SPAN_KIND_CLIENT": 3,
            "SPAN_KIND_PRODUCER": 4,
            "SPAN_KIND_CONSUMER": 5,
        }
        if isinstance(kind, str):
            kind = kinds[kind]
        if isinstance(kind, bool) or not isinstance(kind, int) or not 0 <= kind <= 5:
            raise ValueError("Invalid span kind")
        payload = {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": identifier(value.get("parentSpanId"), 8, optional=True),
            "name": text(value.get("name", "unnamed span"), 160),
            "kind": kind,
            "attributes": attrs,
            "timestamp_unix_nano": str(at),
            "started_at_unix_nano": str(start),
            "duration_ms": (at - start) / 1_000_000,
        }
        status = attrs.get("http.response.status_code", 0)
        if isinstance(status, bool) or not isinstance(status, int) or not 0 <= status <= 599:
            raise ValueError("Invalid HTTP status")
        if status >= 500 or value.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR"):
            severity = "error"
        elif status >= 400:
            severity = "warning"
        summary = (
            f"{payload['name']} completed in {payload['duration_ms']:.3f} ms "
            f"(HTTP {status or 'unknown'})"
        )
        locator = f"traces/{trace_id}/spans/{span_id}"
    elif source == "log":
        level = value.get("severityNumber", 9)
        if isinstance(level, str) and level.startswith("SEVERITY_NUMBER_"):
            if level == "SEVERITY_NUMBER_UNSPECIFIED":
                level = 0
            else:
                match = re.fullmatch(
                    r"SEVERITY_NUMBER_(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)([2-4]?)", level
                )
                if match is None:
                    raise ValueError("Invalid severity enum")
                base = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}[
                    match[1]
                ]
                level = base + (int(match[2]) - 1 if match[2] else 0)
        level = uint(level)
        if level > 24:
            raise ValueError("Invalid log severity")
        severity = (
            "info"
            if level == 0
            else ("trace", "debug", "info", "warning", "error", "critical")[max(level - 1, 0) // 4]
        )
        payload = {
            "message": text(value.get("body", {}).get("stringValue", "Unstructured log")),
            "timestamp_basis": "event_time"
            if uint(value.get("timeUnixNano", 0))
            else "observed_time",
            "attributes": attrs,
            "timestamp_unix_nano": str(at),
            "trace_id": identifier(value.get("traceId"), 16, optional=True),
            "span_id": identifier(value.get("spanId"), 8, optional=True),
        }
        summary = payload["message"]
        locator = f"logs/{at}/{digest(payload)}"
        if attrs.get("event.name") == "deployment":
            event_id = str(UUID(attrs["deployment.id"]))
            revision = attrs["deployment.revision"]
            previous = attrs["deployment.previous_revision"]
            if (
                summary != "deployment_changed"
                or attrs.get("deployment.state") != "succeeded"
                or revision not in ("checkout-v1", "checkout-v2")
                or previous not in ("checkout-v1", "checkout-v2")
                or revision == previous
            ):
                raise ValueError("Invalid structured deployment event")
            source = "deployment"
            payload.update(
                deployment_id=event_id,
                revision=revision,
                previous_revision=previous,
                state="succeeded",
            )
            summary = f"Deployment changed {previous} to {revision}"
            locator = f"deployments/{event_id}"
    else:
        if uint(value.get("flags", 0)) & 1:
            raise ValueError("Metric explicitly has no recorded value")
        payload = metric_payload(observation, value, attrs, at)
        summary = (
            f"{payload['name']} "
            f"{payload.get('aggregation_temporality', 'instantaneous')} "
            f"{payload['kind']} snapshot"
        )
        locator = f"metrics/{quote(payload['name'], safe='')}/{at}/{digest(payload)}"
    source_uri = f"otel://{service}/{locator}"
    content_hash = digest(
        {
            "source_type": source,
            "service": service,
            "source_uri": source_uri,
            "severity": severity,
            "summary": summary,
            "payload": payload,
        }
    )
    record_id = uuid5(NAMESPACE_URL, f"incidentiq:evidence:v1:{incident_id}:{content_hash}")
    return EvidenceRecord(
        id=record_id,
        incident_id=incident_id,
        source_type=source,
        service=service,
        timestamp=timestamp(at),
        severity=severity,
        summary=summary,
        payload=payload,
        source_uri=source_uri,
        raw_locator=raw_locator,
        content_hash=content_hash,
    )
