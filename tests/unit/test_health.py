import json
import logging
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from incidentiq.core.config import Settings
from incidentiq.core.logging import JsonFormatter
from incidentiq.db.session import SCHEMA_REVISION
from incidentiq.main import create_app
from sqlalchemy.exc import OperationalError


@pytest.fixture
def engine():
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = (
        SCHEMA_REVISION
    )
    return engine


@pytest.fixture
def client(monkeypatch, engine):
    monkeypatch.setattr("incidentiq.main.build_engine", lambda settings: engine)
    with TestClient(create_app(Settings(_env_file=None))) as client:
        yield client
    engine.dispose.assert_called_once()


def test_liveness_does_not_require_database(client, engine):
    engine.connect.side_effect = AssertionError("Liveness must not query the database")
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "incidentiq-api"}
    UUID(response.headers["X-Request-ID"])


def test_readiness_requires_expected_migration(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "schema": "ok"}


@pytest.mark.parametrize("revision", [None, "older_revision"])
def test_readiness_rejects_missing_or_old_schema(client, engine, revision):
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = (
        revision
    )
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_not_ready"


def test_readiness_failure_does_not_expose_connection_details(client, engine, capsys):
    secret = "synthetic-password-must-not-leak"
    engine.connect.side_effect = OperationalError("SELECT secret", {}, Exception(secret))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert secret not in response.text
    assert secret not in capsys.readouterr().err
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_request_id_is_preserved_and_errors_are_consistent(client):
    request_id = str(uuid4())
    response = client.get("/missing", headers={"X-Request-ID": request_id})
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "error": {"code": "http_404", "message": "Not Found", "request_id": request_id}
    }


def test_invalid_request_id_is_replaced(client):
    response = client.get("/health/live", headers={"X-Request-ID": "untrusted-string"})
    UUID(response.headers["X-Request-ID"])


def test_unexpected_failure_returns_safe_error(client, engine, capsys):
    secret = "synthetic-internal-detail"
    engine.connect.side_effect = RuntimeError(secret)
    response = client.get("/health/ready")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert secret not in response.text
    assert secret not in capsys.readouterr().err


def test_log_formatter_outputs_utc_and_request_fields():
    record = logging.LogRecord("incidentiq", logging.INFO, "", 0, "request_completed", (), None)
    record.request_id = "example"
    record.status_code = 200
    payload = json.loads(JsonFormatter().format(record))
    assert payload["timestamp"].endswith("+00:00")
    assert payload["request_id"] == "example"
    assert payload["status_code"] == 200


def test_settings_handle_reserved_characters_in_password():
    password = "synthetic:p@ss%word/with?characters"
    settings = Settings(_env_file=None, postgres_password=password)
    assert settings.database_url.password == password
    assert password not in repr(settings)
    assert password not in str(settings.database_url)
