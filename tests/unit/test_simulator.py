import json
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from incidentiq.simulator.config import SimulatorSettings
from incidentiq.simulator.deployments import (
    DeploymentCapacityError,
    DeploymentRegistry,
    DeploymentRequest,
)
from incidentiq.simulator.scenarios import run_deployment_regression, run_slow_dependency
from incidentiq.simulator.service import create_app
from incidentiq.simulator.telemetry import Telemetry
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode


def telemetry(service):
    spans = InMemorySpanExporter()
    logs = InMemoryLogRecordExporter()
    metrics = InMemoryMetricReader()
    return (
        Telemetry(service, span_exporter=spans, log_exporter=logs, metric_reader=metrics),
        spans,
        logs,
        metrics,
    )


@contextmanager
def service_pair():
    inventory, inventory_spans, inventory_logs, inventory_metrics = telemetry("inventory")
    checkout, checkout_spans, checkout_logs, checkout_metrics = telemetry("checkout")
    inventory_app = create_app(
        SimulatorSettings(_env_file=None, role="inventory"), telemetry=inventory
    )
    checkout_app = create_app(
        SimulatorSettings(_env_file=None, role="checkout"),
        telemetry=checkout,
        transport=httpx.ASGITransport(app=inventory_app),
    )
    with TestClient(inventory_app), TestClient(checkout_app) as client:
        yield (
            client,
            {
                "inventory_spans": inventory_spans,
                "checkout_spans": checkout_spans,
                "inventory_logs": inventory_logs,
                "checkout_logs": checkout_logs,
                "inventory_metrics": inventory_metrics,
                "checkout_metrics": checkout_metrics,
            },
        )


def test_request_trace_and_logs_correlate_across_real_asgi_services():
    request_id, run_id = str(uuid4()), str(uuid4())
    with service_pair() as (client, signals):
        response = client.post(
            "/checkout",
            json={"run_id": run_id, "delay_ms": 0},
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code == 200
        assert response.json()["run_id"] == run_id
        spans = signals["checkout_spans"].get_finished_spans()
        root = next(span for span in spans if span.kind == SpanKind.SERVER)
        downstream = next(span for span in spans if span.kind == SpanKind.CLIENT)
        inventory = signals["inventory_spans"].get_finished_spans()[0]
        assert len(spans) == 2
        assert downstream.parent.span_id == root.context.span_id
        assert inventory.parent.span_id == downstream.context.span_id
        assert root.context.trace_id == downstream.context.trace_id == inventory.context.trace_id
        assert response.headers["X-Trace-ID"] == f"{root.context.trace_id:032x}"
        assert response.headers["X-Request-ID"] == request_id
        for service in ("checkout", "inventory"):
            records = signals[f"{service}_logs"].get_finished_logs()
            assert len(records) == 1
            record = records[0].log_record
            assert record.trace_id == root.context.trace_id
            assert record.attributes["request_id"] == request_id
            assert record.attributes["run_id"] == run_id
            assert records[0].resource.attributes["service.name"] == service


def test_fault_delay_appears_in_downstream_span_and_histogram_without_errors():
    with service_pair() as (client, signals):
        for delay in (0, 80):
            response = client.post("/checkout", json={"run_id": str(uuid4()), "delay_ms": delay})
            assert response.status_code == 200
        inventory_spans = signals["inventory_spans"].get_finished_spans()
        slow = inventory_spans[-1]
        assert (slow.end_time - slow.start_time) / 1e9 >= 0.06
        assert slow.attributes["http.response.status_code"] == 200
        for service in ("checkout", "inventory"):
            data = signals[f"{service}_metrics"].get_metrics_data()
            histogram = data.resource_metrics[0].scope_metrics[0].metrics[0]
            assert histogram.name == "incidentiq.request.duration"
            point = histogram.data.data_points[0]
            assert point.count == 2
            assert point.sum >= 0.06
            assert point.attributes["http.response.status_code"] == 200
            expected = {"http.route", "http.response.status_code"}
            if service == "checkout":
                expected.add("deployment.revision")
                assert point.attributes["deployment.revision"] == "checkout-v1"
            assert set(point.attributes) == expected


def test_external_w3c_parent_is_preserved():
    trace_id = "0123456789abcdef0123456789abcdef"
    parent_id = "0123456789abcdef"
    with service_pair() as (client, signals):
        response = client.post(
            "/checkout",
            json={"run_id": str(uuid4()), "delay_ms": 0},
            headers={"traceparent": f"00-{trace_id}-{parent_id}-01"},
        )
        assert response.headers["X-Trace-ID"] == trace_id
        root = next(
            span
            for span in signals["checkout_spans"].get_finished_spans()
            if span.kind == SpanKind.SERVER
        )
        assert root.parent.span_id == int(parent_id, 16)


@pytest.mark.parametrize("delay", [-1, 2001, True, "20", 1.5])
def test_delay_is_strict_and_bounded(delay):
    with service_pair() as (client, signals):
        response = client.post("/checkout", json={"run_id": str(uuid4()), "delay_ms": delay})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert not signals["inventory_spans"].get_finished_spans()


def test_caller_cannot_supply_downstream_url_or_unknown_arguments():
    with service_pair() as (client, signals):
        response = client.post(
            "/checkout",
            json={"run_id": str(uuid4()), "delay_ms": 0, "url": "http://untrusted.invalid/secret"},
        )
        assert response.status_code == 422
        assert "untrusted.invalid" not in response.text
        assert not signals["inventory_spans"].get_finished_spans()


@pytest.mark.parametrize(
    "failure,expected_status",
    [
        ("timeout", 504),
        ("connect", 503),
        ("malformed", 502),
        ("status", 502),
        ("run_mismatch", 502),
    ],
)
def test_downstream_failures_are_safe_and_observable(failure, expected_status):
    secret = "synthetic-connection-detail-must-not-leak"

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure == "connect":
            raise httpx.ConnectError(secret, request=request)
        if failure == "status":
            return httpx.Response(500, text=secret)
        if failure == "run_mismatch":
            return httpx.Response(
                200, json={"service": "inventory", "status": "reserved", "run_id": str(uuid4())}
            )
        return httpx.Response(200, text=secret)

    runtime, spans, logs, metrics = telemetry("checkout")
    app = create_app(
        SimulatorSettings(_env_file=None), telemetry=runtime, transport=httpx.MockTransport(respond)
    )
    with TestClient(app) as client:
        response = client.post("/checkout", json={"run_id": str(uuid4()), "delay_ms": 0})
        assert response.status_code == expected_status
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert secret not in response.text
        for span in spans.get_finished_spans():
            assert span.status.status_code == StatusCode.ERROR
            assert not span.events
        assert secret not in str(logs.get_finished_logs()[0].log_record.attributes)
        data = metrics.get_metrics_data()
        assert (
            data.resource_metrics[0]
            .scope_metrics[0]
            .metrics[0]
            .data.data_points[0]
            .attributes["http.response.status_code"]
            == expected_status
        )


def test_health_does_not_emit_business_telemetry():
    with service_pair() as (client, signals):
        assert client.get("/health/live").status_code == 200
        assert not signals["checkout_spans"].get_finished_spans()
        assert not signals["checkout_logs"].get_finished_logs()


def test_scenario_groups_actual_responses_and_preserves_sample_identifiers():
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={"status": "completed", "run_id": payload["run_id"]},
            headers={"X-Request-ID": request.headers["X-Request-ID"], "X-Trace-ID": uuid4().hex},
        )

    with httpx.Client(base_url="http://checkout", transport=httpx.MockTransport(respond)) as client:
        result = run_slow_dependency(client, requests=2)
    assert [request["delay_ms"] for request in requests] == [20, 20, 500, 500]
    assert result["configuration"]["requests_per_phase"] == 2
    assert result["phases"][0]["run_id"] != result["phases"][1]["run_id"]
    assert all(len(phase["samples"]) == 2 for phase in result["phases"])
    assert result["started_at"].endswith("+00:00")


@pytest.mark.parametrize(
    "arguments",
    [{"requests": 0}, {"requests": 21}, {"fault_ms": 2001}, {"baseline_ms": 500, "fault_ms": 20}],
)
def test_invalid_scenario_configuration_never_sends_requests(arguments):
    def unexpected(request):
        pytest.fail("Invalid scenario must fail before sending requests")

    with httpx.Client(
        base_url="http://checkout", transport=httpx.MockTransport(unexpected)
    ) as client:
        with pytest.raises(ValueError):
            run_slow_dependency(client, **arguments)


def test_deployment_regression_runs_real_service_chain_and_recovers():
    with service_pair() as (client, signals):
        result = run_deployment_regression(client, requests=2)
        assert result["regression_observed"]
        assert [phase["error_rate"] for phase in result["phases"]] == [0, 1, 0]
        assert len({phase["run_id"] for phase in result["phases"]}) == 1
        events = [
            record.log_record
            for record in signals["checkout_logs"].get_finished_logs()
            if record.log_record.body == "deployment_changed"
        ]
        assert len(events) == 2
        assert [event.attributes["deployment.revision"] for event in events] == [
            "checkout-v2",
            "checkout-v1",
        ]
        spans = signals["checkout_spans"].get_finished_spans()
        fault = [span for span in spans if span.attributes["deployment.revision"] == "checkout-v2"]
        assert len(fault) == 4
        for span in fault:
            if span.kind == SpanKind.SERVER:
                assert span.attributes["http.response.status_code"] == 500
                assert span.attributes["error.type"] == "response_contract_error"
                assert span.status.status_code == StatusCode.ERROR
                assert events[0].timestamp < span.start_time < events[1].timestamp
            else:
                assert span.attributes["http.response.status_code"] == 200
                assert span.status.status_code == StatusCode.UNSET
        assert len(signals["inventory_spans"].get_finished_spans()) == 6
        assert all(
            span.attributes["http.response.status_code"] == 200
            for span in signals["inventory_spans"].get_finished_spans()
        )
        data = signals["checkout_metrics"].get_metrics_data()
        points = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points
        assert {
            (
                p.attributes["deployment.revision"],
                p.attributes["http.response.status_code"],
            ): p.count
            for p in points
        } == {("checkout-v1", 200): 4, ("checkout-v2", 500): 2}
        assert all(
            set(p.attributes) == {"http.route", "http.response.status_code", "deployment.revision"}
            for p in points
        )


def test_deployment_controls_are_run_scoped_idempotent_and_strict():
    run_id = str(uuid4())
    with service_pair() as (client, signals):
        first = client.post(
            "/simulator/deployments", json={"run_id": run_id, "revision": "checkout-v2"}
        )
        assert first.status_code == 200
        repeat = client.post(
            "/simulator/deployments", json={"run_id": run_id, "revision": "checkout-v2"}
        )
        assert not repeat.json()["changed"]
        assert first.json()["event"] == repeat.json()["event"]
        assert (
            client.post("/checkout", json={"run_id": str(uuid4()), "delay_ms": 0}).status_code
            == 200
        )
        assert client.post("/checkout", json={"run_id": run_id, "delay_ms": 0}).status_code == 500
        for extra in (
            {"revision": "unknown"},
            {"revision": "checkout-v2", "url": "http://bad.invalid"},
        ):
            assert (
                client.post("/simulator/deployments", json={"run_id": run_id, **extra}).status_code
                == 422
            )
        assert (
            sum(
                record.log_record.body == "deployment_changed"
                for record in signals["checkout_logs"].get_finished_logs()
            )
            == 1
        )


def test_registry_caps_active_overrides_releases_on_rollback_and_expires():
    now = [0.0]
    registry = DeploymentRegistry(max_active=1, ttl_seconds=10, clock=lambda: now[0])
    first, second = uuid4(), uuid4()
    assert registry.revision(first) == "checkout-v1"
    registry.activate(DeploymentRequest(run_id=first, revision="checkout-v2"))
    with pytest.raises(DeploymentCapacityError):
        registry.activate(DeploymentRequest(run_id=second, revision="checkout-v2"))
    registry.activate(DeploymentRequest(run_id=first, revision="checkout-v1"))
    registry.activate(DeploymentRequest(run_id=second, revision="checkout-v2"))
    now[0] = 9
    assert not registry.activate(DeploymentRequest(run_id=second, revision="checkout-v2")).changed
    now[0] = 10
    assert registry.revision(second) == "checkout-v1"
    registry.activate(DeploymentRequest(run_id=first, revision="checkout-v2"))


@pytest.mark.parametrize(
    "failure", ["fault_response", "activation_response_lost", "rollback_response_lost"]
)
def test_scenario_attempts_rollback_after_errors(failure):
    deployed = [False]
    controls = []
    business_calls = [0]

    def respond(request):
        body = json.loads(request.content)
        if request.url.path == "/simulator/deployments":
            controls.append(body["revision"])
            previous = "checkout-v2" if deployed[0] else "checkout-v1"
            deployed[0] = body["revision"] == "checkout-v2"
            if (failure == "activation_response_lost" and deployed[0]) or (
                failure == "rollback_response_lost" and len(controls) == 2
            ):
                raise httpx.ReadTimeout("simulated response loss", request=request)
            return httpx.Response(
                200,
                json={
                    "run_id": body["run_id"],
                    "revision": body["revision"],
                    "changed": previous != body["revision"],
                    "event": None,
                },
            )
        business_calls[0] += 1
        faulty = deployed[0]
        status = 503 if faulty and failure == "fault_response" else 500 if faulty else 200
        return httpx.Response(
            status,
            json={"error": {"code": "response_contract_error"}}
            if faulty
            else {"status": "completed", "run_id": body["run_id"]},
            headers={
                "X-Request-ID": request.headers["X-Request-ID"],
                "X-Trace-ID": uuid4().hex,
                "X-Simulator-Revision": "checkout-v2" if faulty else "checkout-v1",
            },
        )

    with httpx.Client(base_url="http://checkout", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises((ValueError, httpx.ReadTimeout)):
            run_deployment_regression(client, requests=1)
    assert not deployed[0]
    assert controls[-1] == "checkout-v1"
    assert business_calls[0] >= 1


@pytest.mark.parametrize("count", [0, 21])
def test_deployment_scenario_rejects_request_count_before_http(count):
    with httpx.Client(
        base_url="http://checkout",
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Invalid configuration must not call HTTP")
        ),
    ) as client:
        with pytest.raises(ValueError):
            run_deployment_regression(client, requests=count)
