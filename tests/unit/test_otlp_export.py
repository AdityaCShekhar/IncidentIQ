from threading import Lock
from uuid import uuid4

import httpx
import requests
from fastapi.testclient import TestClient
from incidentiq.simulator.config import SimulatorSettings
from incidentiq.simulator.service import create_app
from incidentiq.simulator.telemetry import Telemetry
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def test_sdk_exports_all_three_signals_as_otlp_protobuf(monkeypatch):
    """Exercise the real exporters; only the collector HTTP response is replaced."""
    captured = []
    lock = Lock()

    def receive(session, url, data=None, **kwargs):
        with lock:
            captured.append((url, data))
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        return response

    monkeypatch.setattr(requests.Session, "post", receive)
    run_id, request_id = str(uuid4()), str(uuid4())

    def inventory(request):
        return httpx.Response(
            200, json={"service": "inventory", "status": "reserved", "run_id": run_id}
        )

    runtime = Telemetry("checkout", endpoint="http://collector.invalid:4318")
    app = create_app(
        SimulatorSettings(_env_file=None),
        telemetry=runtime,
        transport=httpx.MockTransport(inventory),
    )
    with TestClient(app) as client:
        response = client.post(
            "/checkout",
            json={"run_id": run_id, "delay_ms": 0},
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code == 200
        trace_id = bytes.fromhex(response.headers["X-Trace-ID"])
        assert runtime.traces.force_flush(timeout_millis=3000)
        assert runtime.logs.force_flush(timeout_millis=3000)
        assert runtime.metrics.force_flush(timeout_millis=3000)

    signals = {}
    for url, payload in captured:
        signals.setdefault(url.rsplit("/", 1)[-1], []).append(payload)
        assert url.startswith("http://collector.invalid:4318/v1/")
    assert set(signals) == {"traces", "logs", "metrics"}
    traces = ExportTraceServiceRequest.FromString(signals["traces"][0])
    spans = traces.resource_spans[0].scope_spans[0].spans
    assert len(spans) == 2
    assert all(span.trace_id == trace_id for span in spans)
    logs = ExportLogsServiceRequest.FromString(signals["logs"][0])
    record = logs.resource_logs[0].scope_logs[0].log_records[0]
    assert record.trace_id == trace_id
    assert record.body.string_value == "request_completed"
    attributes = {item.key: item.value for item in record.attributes}
    assert attributes["request_id"].string_value == request_id
    metrics = ExportMetricsServiceRequest.FromString(signals["metrics"][0])
    metric = metrics.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == "incidentiq.request.duration"
    assert metric.histogram.data_points[0].count == 1
