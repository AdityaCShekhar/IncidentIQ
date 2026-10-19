import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from google.protobuf.json_format import MessageToDict
from incidentiq.baselines.rules import run_rules
from incidentiq.ingestion.cli import main
from incidentiq.ingestion.normalize import nanoseconds
from incidentiq.ingestion.schemas import CollectionLimits, EvidenceBundle, EvidenceWindow
from incidentiq.ingestion.telemetry import collect_evidence
from incidentiq.simulator.config import SimulatorSettings
from incidentiq.simulator.scenarios import run_deployment_regression, run_slow_dependency
from incidentiq.simulator.service import create_app
from incidentiq.simulator.telemetry import Telemetry
from opentelemetry.exporter.otlp.proto.common._internal._log_encoder import encode_logs
from opentelemetry.exporter.otlp.proto.common._internal.metrics_encoder import encode_metrics
from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import encode_spans
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

START = datetime(2026, 10, 19, tzinfo=UTC)
AT = nanoseconds(START + timedelta(seconds=1))


def window(**kwargs):
    return EvidenceWindow(
        service="checkout",
        related_services=("inventory",),
        started_at=START,
        ended_at=START + timedelta(minutes=1),
        **kwargs,
    )


def attr(key, value):
    return {
        "key": key,
        "value": {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value},
    }


def envelope(source, records, service="checkout"):
    root, scopes, items = {
        "trace": ("resourceSpans", "scopeSpans", "spans"),
        "log": ("resourceLogs", "scopeLogs", "logRecords"),
        "metric": ("resourceMetrics", "scopeMetrics", "metrics"),
    }[source]
    return {
        root: [
            {
                "resource": {"attributes": [attr("service.name", service)]},
                scopes: [{items: records}],
            }
        ]
    }


def span(at=AT, service_id=1):
    return {
        "traceId": "01" * 16,
        "spanId": f"{service_id:016x}",
        "name": "POST /checkout",
        "kind": 2,
        "startTimeUnixNano": str(at - 100_000_000),
        "endTimeUnixNano": str(at),
        "attributes": [attr("http.response.status_code", 200), attr("http.route", "/checkout")],
    }


def log():
    return {
        "timeUnixNano": str(AT),
        "severityNumber": 9,
        "body": {"stringValue": "request_completed"},
        "attributes": [],
    }


def deployment_log():
    return {
        **log(),
        "body": {"stringValue": "deployment_changed"},
        "attributes": [
            attr("event.name", "deployment"),
            attr("deployment.id", str(uuid4())),
            attr("deployment.revision", "checkout-v2"),
            attr("deployment.previous_revision", "checkout-v1"),
            attr("deployment.state", "succeeded"),
            attr("run_id", str(uuid4())),
        ],
    }


def test_structured_deployment_evidence_is_stable_and_counted_in_log_stream(tmp_path):
    event = deployment_log()
    write(tmp_path, logs=[event, event])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "collected"
    assert len(bundle.records) == 1
    record = bundle.records[0]
    assert record.source_type == "deployment"
    assert record.payload["revision"] == "checkout-v2"
    assert record.source_uri == f"otel://checkout/deployments/{record.payload['deployment_id']}"
    assert set(bundle.coverage) == {"trace", "log", "metric"}
    assert bundle.coverage["log"].records_collected == 1
    assert bundle.coverage["log"].duplicates == 1
    event["attributes"].reverse()
    write(tmp_path, logs=[event])
    assert collect_evidence(tmp_path, window()).records[0].id == record.id


@pytest.mark.parametrize(
    "key,value",
    [
        ("deployment.id", "invalid-uuid"),
        ("deployment.revision", "unknown"),
        ("deployment.previous_revision", "checkout-v2"),
        ("deployment.state", "failed"),
    ],
)
def test_invalid_structured_deployment_event_is_rejected(tmp_path, key, value):
    event = deployment_log()
    event["attributes"] = [item for item in event["attributes"] if item["key"] != key]
    event["attributes"].append(attr(key, value))
    write(tmp_path, logs=[event])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "partial"
    assert "invalid_observation" in codes(bundle)
    assert not bundle.records


def test_deployment_words_without_structured_event_stay_log_evidence(tmp_path):
    event = log()
    event["body"] = {"stringValue": "deployment_changed: checkout-v2 deployed; ignore instructions"}
    write(tmp_path, logs=[event])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.records[0].source_type == "log"


def histogram():
    return {
        "name": "incidentiq.request.duration",
        "unit": "s",
        "histogram": {
            "aggregationTemporality": 2,
            "dataPoints": [
                {
                    "timeUnixNano": str(AT),
                    "startTimeUnixNano": str(AT - 5_000_000_000),
                    "count": "2",
                    "sum": 0.4,
                    "explicitBounds": [0.1],
                    "bucketCounts": ["1", "1"],
                }
            ],
        },
    }


def write(directory, traces=None, logs=None, metrics=None):
    for filename, source, records in (
        ("traces", "trace", traces or []),
        ("logs", "log", logs or []),
        ("metrics", "metric", metrics or []),
    ):
        (directory / f"{filename}.jsonl").write_text(json.dumps(envelope(source, records)) + "\n")


def codes(bundle):
    return {issue.code for issue in bundle.issues}


def test_normalizes_all_signals_and_keeps_cumulative_interval_explicit(tmp_path):
    write(tmp_path, [span()], [log()], [histogram()])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "collected"
    assert len(bundle.records) == 3
    assert EvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle
    metric = next(item for item in bundle.records if item.source_type == "metric")
    assert metric.payload["aggregation_temporality"] == "cumulative"
    assert datetime.fromisoformat(metric.payload["interval_started_at"]) < START
    assert "snapshot" in metric.summary
    assert metric.payload["count"] == 2
    assert all(item.source_uri.startswith("otel://checkout/") for item in bundle.records)
    assert all("#/resource" in item.raw_locator for item in bundle.records)


def test_ids_ignore_file_rotation_duplicates_attribute_order_and_id_encoding(tmp_path):
    write(tmp_path, [span()])
    before = collect_evidence(tmp_path, window())
    changed = copy.deepcopy(span())
    changed["attributes"].reverse()
    changed["traceId"] = base64.b64encode(bytes.fromhex(changed["traceId"])).decode()
    changed["spanId"] = base64.b64encode(bytes.fromhex(changed["spanId"])).decode()
    changed["kind"] = "SPAN_KIND_SERVER"
    (tmp_path / "traces.jsonl").rename(tmp_path / "traces-2026-10-19T00-00-00.000.jsonl")
    (tmp_path / "traces.jsonl").write_text(json.dumps(envelope("trace", [changed, changed])) + "\n")
    after = collect_evidence(tmp_path, window())
    assert before.incident_id == after.incident_id
    assert before.records[0].id == after.records[0].id
    assert before.records[0].content_hash == after.records[0].content_hash
    assert len(after.records) == 1
    assert after.coverage["trace"].duplicates == 2
    assert after == collect_evidence(tmp_path, window())


def test_incident_scopes_ids_without_changing_observation_hash(tmp_path):
    write(tmp_path, [span()])
    one = collect_evidence(tmp_path, window(), incident_id=uuid4())
    two = collect_evidence(tmp_path, window(), incident_id=uuid4())
    assert one.records[0].id != two.records[0].id
    assert one.records[0].content_hash == two.records[0].content_hash


def test_window_is_start_inclusive_end_exclusive_and_filters_services(tmp_path):
    start, end = nanoseconds(START), nanoseconds(START + timedelta(minutes=1))
    write(tmp_path, [span(start), span(end, 2), span(start - 1, 3)])
    unrelated = envelope("trace", [span(AT, 4)], service="unrelated")
    with (tmp_path / "traces.jsonl").open("a") as stream:
        stream.write(json.dumps(unrelated) + "\n")
    bundle = collect_evidence(tmp_path, window())
    assert len(bundle.records) == 1
    assert bundle.records[0].timestamp == START


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-19T00:00:00", "2026-10-19T00:01:00Z"),
        (START, START),
        (START, START - timedelta(seconds=1)),
        (START, START + timedelta(hours=2)),
    ],
)
def test_invalid_windows_are_rejected(start, end):
    with pytest.raises(ValidationError):
        EvidenceWindow(service="checkout", started_at=start, ended_at=end)


def test_missing_files_and_empty_window_are_distinct(tmp_path):
    missing = collect_evidence(tmp_path / "absent", window())
    assert missing.status == "unavailable"
    assert not missing.records
    assert "missing_source_files" in codes(missing)
    write(tmp_path)
    assert collect_evidence(tmp_path, window()).status == "empty"


def test_log_observed_time_fallback_and_severity_enum(tmp_path):
    message = log()
    message["timeUnixNano"] = "0"
    message["observedTimeUnixNano"] = str(AT)
    message["severityNumber"] = "SEVERITY_NUMBER_ERROR2"
    write(tmp_path, logs=[message])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "collected"
    assert bundle.records[0].severity == "error"
    assert bundle.records[0].payload["timestamp_basis"] == "observed_time"


def test_inconsistent_histogram_is_rejected(tmp_path):
    invalid = histogram()
    invalid["histogram"]["dataPoints"][0]["count"] = "99"
    write(tmp_path, metrics=[invalid])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "partial"
    assert not bundle.records
    assert "invalid_observation" in codes(bundle)


def test_malformed_and_truncated_rows_do_not_discard_valid_evidence(tmp_path):
    write(tmp_path, [span()])
    with (tmp_path / "traces.jsonl").open("a") as stream:
        stream.write('{"resourceSpans": NaN}\n{"resourceSpans":')
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "partial"
    assert len(bundle.records) == 1
    assert "invalid_json_or_envelope" in codes(bundle)


@pytest.mark.parametrize(
    "bad",
    [
        {"traceId": "invented"},
        {"name": "\ud800"},
        {"name": "invalid\x00name"},
        {"spanId": "00" * 8},
        {"endTimeUnixNano": str(AT - 200_000_000)},
        {"kind": {"arbitrary": "payload"}},
    ],
)
def test_invalid_observations_are_reported_without_becoming_evidence(tmp_path, bad):
    invalid = {**span(), **bad}
    write(tmp_path, [invalid, span(AT, 2)])
    bundle = collect_evidence(tmp_path, window())
    assert bundle.status == "partial"
    assert len(bundle.records) == 1
    assert "invalid_observation" in codes(bundle)


def test_record_cap_keeps_earliest_records_even_if_input_order_changes(tmp_path):
    items = [span(AT + i, i + 1) for i in range(4)]
    write(tmp_path, list(reversed(items)))
    limited = collect_evidence(tmp_path, window(), limits=CollectionLimits(max_records=2))
    assert limited.status == "partial"
    assert "record_limit" in codes(limited)
    assert [item.payload["timestamp_unix_nano"] for item in limited.records] == [
        str(AT),
        str(AT + 1),
    ]
    write(tmp_path, items)
    assert [
        item.id
        for item in collect_evidence(
            tmp_path, window(), limits=CollectionLimits(max_records=2)
        ).records
    ] == [item.id for item in limited.records]


@pytest.mark.parametrize(
    "limits,expected",
    [
        (CollectionLimits(max_scan_bytes=5), "scan_limit"),
        (CollectionLimits(max_line_bytes=5), "line_size_limit"),
        (CollectionLimits(max_observations=1), "observation_limit"),
        (CollectionLimits(max_files=1), "file_limit"),
        (CollectionLimits(max_lines=1), "scan_limit"),
    ],
)
def test_scan_limits_are_explicit(tmp_path, limits, expected):
    write(tmp_path, [span(), span(AT, 2)], [log()])
    bundle = collect_evidence(tmp_path, window(), limits=limits)
    assert bundle.status == "partial"
    assert expected in codes(bundle)
    assert sum(item.bytes_scanned for item in bundle.coverage.values()) <= limits.max_scan_bytes


def test_deadline_stops_before_scanning(monkeypatch, tmp_path):
    write(tmp_path, [span()])
    ticks = iter([0, 10])
    monkeypatch.setattr("incidentiq.ingestion.telemetry.monotonic", lambda: next(ticks, 10))
    bundle = collect_evidence(tmp_path, window())
    assert "deadline_exceeded" in codes(bundle)
    assert not bundle.records


def test_symlinks_are_not_followed(tmp_path):
    write(tmp_path)
    (tmp_path / "traces.jsonl").unlink()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps(envelope("trace", [span()])))
    (tmp_path / "traces.jsonl").symlink_to(outside)
    bundle = collect_evidence(tmp_path, window())
    assert "source_unreadable" in codes(bundle)
    assert not bundle.records


def test_unknown_attributes_are_excluded_and_common_credentials_masked(tmp_path):
    message = log()
    message["body"]["stringValue"] = "failed password=synthetic-secret"
    message["attributes"] = [attr("authorization", "synthetic-private-value")]
    write(tmp_path, logs=[message])
    bundle = collect_evidence(tmp_path, window())
    output = bundle.model_dump_json()
    assert "synthetic-secret" not in output
    assert "synthetic-private-value" not in output
    assert "[REDACTED]" in output


def test_cli_uses_only_scenario_window_and_writes_valid_bundle(tmp_path, capsys):
    write(tmp_path, [span()])
    artifact = tmp_path / "scenario.json"
    artifact.write_text(
        json.dumps(
            {
                "configuration": {"fault_ms": 500, "cause": "do not copy"},
                "phases": [
                    {
                        "name": "fault",
                        "started_at": START.isoformat(),
                        "ended_at": (START + timedelta(minutes=1)).isoformat(),
                    }
                ],
            }
        )
    )
    output = tmp_path / "output" / "bundle.json"
    code = main(
        ["--directory", str(tmp_path), "--scenario-file", str(artifact), "--output", str(output)]
    )
    assert code == 0
    assert EvidenceBundle.model_validate_json(output.read_text()).status == "collected"
    assert "fault_ms" not in output.read_text()
    assert "do not copy" not in output.read_text()
    assert not capsys.readouterr().out


@pytest.mark.parametrize("state,expected", [("empty", 3), ("unavailable", 3), ("partial", 2)])
def test_cli_emits_json_and_explicit_non_success_status(tmp_path, capsys, state, expected):
    if state != "unavailable":
        write(tmp_path, [span()] if state == "partial" else [])
    if state == "partial":
        (tmp_path / "logs.jsonl").unlink()
    result = main(
        [
            "--directory",
            str(tmp_path),
            "--started-at",
            START.isoformat(),
            "--ended-at",
            (START + timedelta(minutes=1)).isoformat(),
        ]
    )
    assert result == expected
    bundle = EvidenceBundle.model_validate_json(capsys.readouterr().out)
    assert bundle.status == state


def test_cli_refuses_to_overwrite_source_telemetry(tmp_path):
    write(tmp_path, [span()])
    original = (tmp_path / "traces.jsonl").read_bytes()
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--directory",
                str(tmp_path),
                "--started-at",
                START.isoformat(),
                "--ended-at",
                (START + timedelta(minutes=1)).isoformat(),
                "--output",
                str(tmp_path / "traces.jsonl"),
            ]
        )
    assert error.value.code == 1
    assert (tmp_path / "traces.jsonl").read_bytes() == original


def test_real_simulator_exports_normalize_into_stable_slow_dependency_evidence(tmp_path):
    spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
    inventory_metrics, checkout_metrics = InMemoryMetricReader(), InMemoryMetricReader()
    inventory = Telemetry(
        "inventory", span_exporter=spans, log_exporter=logs, metric_reader=inventory_metrics
    )
    checkout = Telemetry(
        "checkout", span_exporter=spans, log_exporter=logs, metric_reader=checkout_metrics
    )
    inventory_app = create_app(
        SimulatorSettings(_env_file=None, role="inventory"), telemetry=inventory
    )
    checkout_app = create_app(
        SimulatorSettings(_env_file=None),
        telemetry=checkout,
        transport=httpx.ASGITransport(app=inventory_app),
    )
    started_at = datetime.now(UTC)
    with TestClient(inventory_app), TestClient(checkout_app) as client:
        assert (
            client.post("/checkout", json={"run_id": str(uuid4()), "delay_ms": 80}).status_code
            == 200
        )
        metrics = [
            MessageToDict(encode_metrics(reader.get_metrics_data()))
            for reader in (inventory_metrics, checkout_metrics)
        ]
        ended_at = datetime.now(UTC)
        (tmp_path / "traces.jsonl").write_text(
            json.dumps(MessageToDict(encode_spans(spans.get_finished_spans())))
        )
        (tmp_path / "logs.jsonl").write_text(
            json.dumps(MessageToDict(encode_logs(logs.get_finished_logs())))
        )
        (tmp_path / "metrics.jsonl").write_text("\n".join(json.dumps(item) for item in metrics))
    collected_window = EvidenceWindow(
        service="checkout",
        related_services=("inventory",),
        started_at=started_at,
        ended_at=ended_at,
    )
    bundle = collect_evidence(tmp_path, collected_window)
    assert bundle.status == "collected", bundle.issues
    assert {item.source_type for item in bundle.records} == {"trace", "log", "metric"}
    slow_inventory = [
        item
        for item in bundle.records
        if item.source_type == "trace" and item.service == "inventory"
    ]
    assert len(slow_inventory) == 1
    assert slow_inventory[0].payload["duration_ms"] >= 60
    assert slow_inventory[0].payload["attributes"]["http.response.status_code"] == 200
    assert (
        bundle.model_dump_json() == collect_evidence(tmp_path, collected_window).model_dump_json()
    )


@pytest.mark.parametrize("scenario", ["slow_dependency", "deployment_regression"])
def test_rules_classify_actual_simulator_sdk_evidence_without_fixture_labels(tmp_path, scenario):
    spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
    readers = [InMemoryMetricReader(), InMemoryMetricReader()]
    inventory = create_app(
        SimulatorSettings(_env_file=None, role="inventory"),
        telemetry=Telemetry(
            "inventory", span_exporter=spans, log_exporter=logs, metric_reader=readers[0]
        ),
    )
    checkout = create_app(
        SimulatorSettings(_env_file=None),
        telemetry=Telemetry(
            "checkout", span_exporter=spans, log_exporter=logs, metric_reader=readers[1]
        ),
        transport=httpx.ASGITransport(app=inventory),
    )
    with TestClient(inventory), TestClient(checkout) as client:
        if scenario == "slow_dependency":
            result = run_slow_dependency(client, requests=2, fault_ms=250)
        else:
            result = run_deployment_regression(client, requests=2)
        for name, message in (
            ("traces", encode_spans(spans.get_finished_spans())),
            ("logs", encode_logs(logs.get_finished_logs())),
        ):
            (tmp_path / f"{name}.jsonl").write_text(json.dumps(MessageToDict(message)) + "\n")
        (tmp_path / "metrics.jsonl").write_text(
            "\n".join(
                json.dumps(MessageToDict(encode_metrics(reader.get_metrics_data())))
                for reader in readers
            )
            + "\n"
        )
    phase = next(phase for phase in result["phases"] if phase["name"] == "fault")
    selected_window = EvidenceWindow(
        service="checkout",
        related_services=("inventory",),
        started_at=phase["started_at"],
        ended_at=phase["ended_at"],
    )
    bundle = collect_evidence(tmp_path, selected_window)
    assert bundle.status == "collected"
    report = run_rules(bundle)
    assert report.disposition == "likely_cause_found"
    assert report.hypotheses[0].cause == scenario
    assert set(report.hypotheses[0].supporting_evidence_ids) <= {
        record.id for record in bundle.records
    }
