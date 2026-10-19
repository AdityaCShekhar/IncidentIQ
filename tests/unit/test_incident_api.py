import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from google.protobuf.json_format import MessageToDict
from incidentiq.api.schemas import IncidentCreate, IncidentResponse
from incidentiq.core.config import Settings
from incidentiq.db.models import Base, EvidenceRow, Incident
from incidentiq.db.repositories import IncidentRepository
from incidentiq.ingestion.normalize import nanoseconds
from incidentiq.ingestion.telemetry import collect_evidence
from incidentiq.main import create_app
from incidentiq.simulator.config import SimulatorSettings
from incidentiq.simulator.scenarios import run_deployment_regression
from incidentiq.simulator.service import create_app as create_simulator
from incidentiq.simulator.telemetry import Telemetry
from opentelemetry.exporter.otlp.proto.common._internal._log_encoder import encode_logs
from opentelemetry.exporter.otlp.proto.common._internal.metrics_encoder import encode_metrics
from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import encode_spans
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from sqlalchemy.orm import Session

START = datetime(2026, 10, 19, tzinfo=UTC)


def payload(**updates):
    return {
        "title": "Checkout slow",
        "service": "checkout",
        "related_services": ["inventory"],
        "started_at": START.isoformat(),
        "ended_at": (START + timedelta(minutes=1)).isoformat(),
        "severity": "high",
        "scenario_id": "slow_dependency",
        **updates,
    }


def write_telemetry(directory):
    directory.mkdir()
    traces = []
    for index, service in enumerate(("checkout", "inventory"), start=1):
        traces.append(
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": service}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "01" * 16,
                                "spanId": f"{index:016x}",
                                "name": "POST /checkout"
                                if service == "checkout"
                                else "POST /reserve",
                                "startTimeUnixNano": str(nanoseconds(START)),
                                "endTimeUnixNano": str(nanoseconds(START + timedelta(seconds=1))),
                                "attributes": [
                                    {
                                        "key": "http.response.status_code",
                                        "value": {"intValue": "200"},
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        )
    (directory / "traces.jsonl").write_text(json.dumps({"resourceSpans": traces}) + "\n")
    (directory / "logs.jsonl").write_text(
        json.dumps(
            {
                "resourceLogs": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": "checkout"}}
                            ]
                        },
                        "scopeLogs": [
                            {
                                "logRecords": [
                                    {
                                        "timeUnixNano": str(
                                            nanoseconds(START + timedelta(seconds=2))
                                        ),
                                        "severityNumber": 13,
                                        "body": {"stringValue": "Synthetic warning"},
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        )
        + "\n"
    )
    (directory / "metrics.jsonl").write_text('{"resourceMetrics": []}\n')


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """Exercise real ORM transactions offline; PostgreSQL semantics need integration tests."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    directory = tmp_path / "telemetry"
    write_telemetry(directory)
    settings = Settings(_env_file=None, telemetry_directory=directory)
    monkeypatch.setattr("incidentiq.main.build_engine", lambda settings: engine)
    with TestClient(create_app(settings)) as client:
        yield client, engine, settings
    engine.dispose()


def test_deployment_scenario_sdk_exports_are_collected_persisted_and_filterable(stack):
    api, _, settings = stack
    spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
    readers = [InMemoryMetricReader(), InMemoryMetricReader()]
    inventory = create_simulator(
        SimulatorSettings(_env_file=None, role="inventory"),
        telemetry=Telemetry(
            "inventory", span_exporter=spans, log_exporter=logs, metric_reader=readers[0]
        ),
    )
    checkout = create_simulator(
        SimulatorSettings(_env_file=None),
        telemetry=Telemetry(
            "checkout", span_exporter=spans, log_exporter=logs, metric_reader=readers[1]
        ),
        transport=httpx.ASGITransport(app=inventory),
    )
    with TestClient(inventory), TestClient(checkout) as client:
        result = run_deployment_regression(client, requests=1)
        for name, message in (
            ("traces", encode_spans(spans.get_finished_spans())),
            ("logs", encode_logs(logs.get_finished_logs())),
        ):
            (settings.telemetry_directory / f"{name}.jsonl").write_text(
                json.dumps(MessageToDict(message)) + "\n"
            )
        (settings.telemetry_directory / "metrics.jsonl").write_text(
            "\n".join(
                json.dumps(MessageToDict(encode_metrics(reader.get_metrics_data())))
                for reader in readers
            )
            + "\n"
        )
    fault = result["phases"][1]
    response = api.post(
        "/incidents",
        json=payload(
            title="Checkout deployment regression",
            scenario_id="deployment_regression",
            started_at=fault["started_at"],
            ended_at=fault["ended_at"],
        ),
    )
    assert response.status_code == 201, response.text
    incident = response.json()
    assert incident["status"] == "ready"
    assert incident["collection"]["status"] == "collected"
    path = f"/incidents/{incident['id']}/evidence"
    events = api.get(path, params={"source": "deployment"}).json()
    assert events["total"] == 1
    event = events["items"][0]
    assert event["payload"]["deployment_id"] == fault["deployment"]["event"]["id"]
    assert event["payload"]["revision"] == "checkout-v2"
    records = api.get(path, params={"source": "trace"}).json()["items"]
    assert len(records) == 3
    checkout_error = next(
        record
        for record in records
        if record["payload"]["attributes"]["http.response.status_code"] == 500
    )
    assert checkout_error["service"] == "checkout"
    assert checkout_error["payload"]["attributes"]["error.type"] == "response_contract_error"
    assert int(event["payload"]["timestamp_unix_nano"]) < int(
        checkout_error["payload"]["started_at_unix_nano"]
    )
    assert {record["payload"]["trace_id"] for record in records} == {
        checkout_error["payload"]["trace_id"]
    }
    for source in settings.telemetry_directory.iterdir():
        source.unlink()
    assert api.get(path, params={"source": "deployment"}).json() == events


def test_create_persists_incident_and_evidence_with_location_and_request_id(stack):
    client, engine, _ = stack
    response = client.post("/incidents", json=payload())
    assert response.status_code == 201, response.text
    incident = IncidentResponse.model_validate(response.json())
    assert incident.status == "ready"
    assert incident.collection.status == "collected"
    assert incident.evidence_count == 3
    assert incident.created_at.utcoffset() == timedelta(0)
    assert response.headers["Location"] == f"/incidents/{incident.id}"
    UUID(response.headers["X-Request-ID"])
    with Session(engine) as session:
        assert session.get(Incident, incident.id).status == "ready"
        assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 3


def test_reads_persisted_evidence_after_source_files_are_removed_and_app_restarts(stack):
    client, _, settings = stack
    created = client.post("/incidents", json=payload()).json()
    before = client.get(f"/incidents/{created['id']}/evidence").json()
    for path in settings.telemetry_directory.iterdir():
        path.unlink()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/incidents/{created['id']}").json() == created
        after = restarted.get(f"/incidents/{created['id']}/evidence").json()
        assert after == before
        assert len(after["items"]) == 3


def test_filters_and_pagination_are_stable_and_confined_to_incident(stack):
    client, _, _ = stack
    one = client.post("/incidents", json=payload()).json()["id"]
    two = client.post("/incidents", json=payload()).json()["id"]
    url = f"/incidents/{one}/evidence"
    filtered = client.get(
        url, params={"service": "inventory", "source": "trace", "severity": "info"}
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["service"] == "inventory"
    assert filtered["items"][0]["incident_id"] == one
    warning = client.get(url, params={"severity": "warning"}).json()
    assert warning["total"] == 1
    pages = [client.get(url, params={"limit": 1, "offset": offset}).json() for offset in range(3)]
    assert all(page["total"] == 3 for page in pages)
    assert [page["has_more"] for page in pages] == [True, True, False]
    ids = [page["items"][0]["id"] for page in pages]
    assert len(set(ids)) == 3
    other_ids = {item["id"] for item in client.get(f"/incidents/{two}/evidence").json()["items"]}
    assert not set(ids) & other_ids
    assert client.get(url, params={"offset": 1000}).json()["items"] == []


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 1001},
        {"source": "shell"},
        {"severity": "anything"},
        {"service": "'; DROP TABLE incidents;--"},
    ],
)
def test_invalid_filters_are_rejected(stack, query):
    client, _, _ = stack
    response = client.get(f"/incidents/{uuid4()}/evidence", params=query)
    assert response.status_code == 422
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "updates",
    [
        {"started_at": "2026-10-19T00:00:00"},
        {"ended_at": START.isoformat()},
        {"ended_at": (START + timedelta(hours=2)).isoformat()},
        {"title": " "},
        {"title": "invalid\x00title"},
        {"description": "invalid\x00description"},
        {"scenario_id": "invalid\x00scenario"},
        {"severity": "invented"},
        {"service": "../../etc"},
        {"telemetry_directory": "/etc"},
    ],
)
def test_invalid_creation_is_rejected_without_writing_incident(stack, updates):
    client, engine, _ = stack
    response = client.post("/incidents", json=payload(**updates))
    assert response.status_code == 422
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 0


@pytest.mark.parametrize("suffix", ["", "/evidence"])
def test_unknown_incident_is_consistent_404(stack, suffix):
    client, _, _ = stack
    response = client.get(f"/incidents/{uuid4()}{suffix}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "incident_not_found"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_invalid_unicode_json_escape_is_rejected_before_database_write(stack):
    client, engine, _ = stack
    response = client.post(
        "/incidents",
        content=json.dumps(payload(title="\ud800")),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 0


def test_unavailable_telemetry_keeps_failed_incident_and_empty_page(stack):
    client, _, settings = stack
    for file in settings.telemetry_directory.iterdir():
        file.unlink()
    response = client.post("/incidents", json=payload())
    assert response.status_code == 201
    incident = response.json()
    assert incident["status"] == "failed"
    assert incident["collection"]["status"] == "unavailable"
    assert incident["evidence_count"] == 0
    assert incident["collection"]["issues"]
    assert client.get(f"/incidents/{incident['id']}/evidence").json()["total"] == 0


def test_partial_evidence_remains_explicit(stack):
    client, _, settings = stack
    (settings.telemetry_directory / "logs.jsonl").unlink()
    incident = client.post("/incidents", json=payload()).json()
    assert incident["status"] == "ready"
    assert incident["collection"]["status"] == "partial"
    assert incident["evidence_count"] == 2


def test_collector_exception_is_persisted_safely(stack, capsys):
    client, _, _ = stack
    secret = "synthetic-internal-detail"

    def broken(*args, **kwargs):
        raise RuntimeError(secret)

    client.app.state.collector = broken
    response = client.post("/incidents", json=payload())
    assert response.status_code == 201
    incident = response.json()
    assert incident["status"] == "failed"
    assert incident["collection"]["issues"][0]["code"] == "collector_failed"
    assert secret not in response.text
    assert secret not in capsys.readouterr().err


def test_collector_cannot_persist_evidence_for_different_incident(stack):
    client, _, _ = stack
    real = client.app.state.collector

    def wrong_scope(directory, window, **kwargs):
        return real(directory, window, incident_id=uuid4())

    client.app.state.collector = wrong_scope
    incident = client.post("/incidents", json=payload()).json()
    assert incident["status"] == "failed"
    assert incident["evidence_count"] == 0


@pytest.mark.parametrize(
    "error,code",
    [
        (OperationalError("query", {}, Exception("synthetic-secret")), "database_unavailable"),
        (DatabaseTimeout("synthetic-secret"), "database_timeout"),
    ],
)
def test_database_failures_return_safe_503(stack, monkeypatch, error, code):
    client, _, _ = stack

    def fail(*args):
        raise error

    monkeypatch.setattr(IncidentRepository, "get", fail)
    response = client.get(f"/incidents/{uuid4()}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code
    assert "synthetic-secret" not in response.text


def test_failed_finalization_returns_recoverable_incident_id(stack, monkeypatch):
    client, _, _ = stack

    def fail(*args):
        raise OperationalError("query", {}, Exception("synthetic-secret"))

    monkeypatch.setattr(IncidentRepository, "save_bundle", fail)
    response = client.post("/incidents", json=payload())
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "evidence_persistence_failed"
    assert "synthetic-secret" not in response.text
    saved = client.get(f"/incidents/{error['incident_id']}").json()
    assert saved["status"] == "collecting"
    assert saved["evidence_count"] == 0


def test_repeated_save_is_idempotent_and_does_not_duplicate_records(stack):
    _, engine, settings = stack
    data = IncidentCreate.model_validate(payload())
    with Session(engine, expire_on_commit=False) as session:
        repo = IncidentRepository(session)
        incident = repo.create(data)
        bundle = collect_evidence(
            settings.telemetry_directory, data.evidence_window(), incident_id=incident.id
        )
        repo.save_bundle(incident.id, bundle)
        repo.save_bundle(incident.id, bundle)
        assert incident.evidence_count == 3
        assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 3


def test_evidence_insert_and_status_are_atomic_on_constraint_failure(stack):
    _, engine, settings = stack
    data = IncidentCreate.model_validate(payload())
    with Session(engine, expire_on_commit=False) as session:
        repo = IncidentRepository(session)
        incident = repo.create(data)
        incident_id = incident.id
        bundle = collect_evidence(
            settings.telemetry_directory, data.evidence_window(), incident_id=incident_id
        )
        bundle.records[1] = bundle.records[1].model_copy(update={"id": bundle.records[0].id})
        with pytest.raises(IntegrityError):
            repo.save_bundle(incident_id, bundle)
        assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 0
        assert session.get(Incident, incident_id).status == "collecting"


def test_sql_characters_in_title_are_stored_as_data(stack):
    client, _, _ = stack
    title = "Checkout's latency; SELECT synthetic FROM examples"
    response = client.post("/incidents", json=payload(title=title))
    assert response.status_code == 201
    assert client.get(response.headers["Location"]).json()["title"] == title
