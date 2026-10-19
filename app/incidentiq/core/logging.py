import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "service": getattr(record, "service", "incidentiq-api"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "method",
            "status_code",
            "latency_ms",
            "error_type",
            "trace_id",
            "span_id",
            "run_id",
            "operation",
            "event.name",
            "deployment.id",
            "deployment.revision",
            "deployment.previous_revision",
            "deployment.state",
            "error.type",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        # Exception strings can include database connection details or raw input.
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
