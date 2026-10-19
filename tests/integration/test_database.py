import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from incidentiq.core.config import Settings
from incidentiq.db.models import EvidenceRow, Incident
from incidentiq.db.session import build_engine, database_is_ready
from incidentiq.ingestion.normalize import nanoseconds
from incidentiq.main import create_app
from sqlalchemy import inspect, make_url, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_migration_persistence_constraints_and_readiness(monkeypatch, tmp_path):
    raw_url = os.environ.get("INCIDENTIQ_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "Set INCIDENTIQ_TEST_DATABASE_URL to an empty PostgreSQL database ending in _test"
        )
    url = make_url(raw_url)
    if url.drivername != "postgresql+psycopg2" or not (url.database or "").endswith("_test"):
        pytest.fail("Integration tests require postgresql+psycopg2 and a database ending in _test")

    for key, value in {
        "DATABASE_HOST": url.host or "127.0.0.1",
        "DATABASE_PORT": str(url.port or 5432),
        "POSTGRES_DB": url.database,
        "POSTGRES_USER": url.username or "incidentiq",
        "POSTGRES_PASSWORD": url.password or "",
    }.items():
        monkeypatch.setenv(key, value)

    directory = tmp_path / "telemetry"
    directory.mkdir()
    settings = Settings(_env_file=None, telemetry_directory=directory)
    engine = build_engine(settings)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        if inspect(engine).get_table_names():
            pytest.fail(
                "Integration test database must be empty; refusing to alter existing tables"
            )
        assert not database_is_ready(engine)
        command.upgrade(config, "head")
        command.upgrade(config, "head")  # Re-running startup migration must be harmless.
        assert database_is_ready(engine)
        command.check(config)  # ORM and migrations must describe the same schema.
        now = datetime.now(UTC)
        with Session(engine) as session:
            incident = Incident(
                title="Synthetic test incident",
                service="checkout",
                severity="high",
                started_at=now,
                ended_at=now + timedelta(minutes=5),
            )
            session.add(incident)
            session.commit()
            incident_id = incident.id
        engine.dispose()  # Read through a new connection, not the insertion session.
        with Session(engine) as session:
            stored = session.scalar(select(Incident).where(Incident.id == incident_id))
            assert stored is not None
            assert stored.status == "created"
            assert stored.started_at == now
            assert stored.created_at.tzinfo is not None
            session.add(
                Incident(
                    title="Invalid",
                    service="checkout",
                    severity="high",
                    started_at=now,
                    ended_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        with TestClient(create_app(settings)) as client:
            assert client.get("/health/ready").status_code == 200
            # Actual PostgreSQL UUID/JSONB/timestamptz and HTTP create/read/pagination path.
            span = {
                "traceId": "01" * 16,
                "spanId": "01" * 8,
                "name": "POST /checkout",
                "startTimeUnixNano": str(nanoseconds(now)),
                "endTimeUnixNano": str(nanoseconds(now + timedelta(seconds=1))),
            }
            (directory / "traces.jsonl").write_text(
                json.dumps(
                    {
                        "resourceSpans": [
                            {
                                "resource": {
                                    "attributes": [
                                        {
                                            "key": "service.name",
                                            "value": {"stringValue": "checkout"},
                                        }
                                    ]
                                },
                                "scopeSpans": [{"spans": [span]}],
                            }
                        ]
                    }
                )
                + "\n"
            )
            (directory / "logs.jsonl").write_text('{"resourceLogs": []}\n')
            (directory / "metrics.jsonl").write_text('{"resourceMetrics": []}\n')
            created = client.post(
                "/incidents",
                json={
                    "title": "PostgreSQL contract check",
                    "service": "checkout",
                    "severity": "high",
                    "started_at": now.isoformat(),
                    "ended_at": (now + timedelta(minutes=5)).isoformat(),
                },
            )
            assert created.status_code == 201, created.text
            metadata = created.json()
            assert metadata["status"] == "ready"
            assert metadata["collection"]["status"] == "collected"
            assert metadata["evidence_count"] == 1
            url = f"/incidents/{metadata['id']}"
            page = client.get(
                f"{url}/evidence", params={"service": "checkout", "source": "trace", "limit": 1}
            ).json()
            assert page["total"] == 1
            assert page["items"][0]["payload"]["duration_ms"] == 1000.0
            with Session(engine) as session:
                stored_evidence = session.get(EvidenceRow, UUID(page["items"][0]["id"]))
                assert stored_evidence.timestamp.tzinfo is not None
                assert stored_evidence.payload["trace_id"] == "01" * 16
            for path in directory.iterdir():
                path.unlink()
            assert client.get(url).json() == metadata
            assert (
                client.get(f"{url}/evidence", params={"source": "trace", "limit": 1}).json() == page
            )
        command.downgrade(config, "base")
        assert "incidents" not in inspect(engine).get_table_names()
        assert not database_is_ready(engine)
        with TestClient(create_app(settings)) as client:
            assert client.get("/health/ready").status_code == 503
    finally:
        engine.dispose()
