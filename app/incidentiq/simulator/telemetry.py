import logging
from time import time_ns

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    LogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import get_current_span

from incidentiq.simulator.deployments import DeploymentEvent


class Telemetry:
    """Own providers per service; avoid global provider changes during ASGI tests."""

    def __init__(
        self,
        service: str,
        endpoint: str | None = None,
        *,
        span_exporter: SpanExporter | None = None,
        log_exporter: LogRecordExporter | None = None,
        metric_reader: MetricReader | None = None,
    ) -> None:
        self.service = service
        resource = Resource.create({"service.name": service, "service.version": "0.1.0"})
        self.traces = TracerProvider(resource=resource)
        self.logs = LoggerProvider(resource=resource)
        readers = []
        if endpoint is not None:
            endpoint = endpoint.rstrip("/")
            self.traces.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=2),
                    max_queue_size=512,
                    max_export_batch_size=128,
                    schedule_delay_millis=500,
                )
            )
            self.logs.add_log_record_processor(
                BatchLogRecordProcessor(
                    OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=2),
                    max_queue_size=512,
                    max_export_batch_size=128,
                    schedule_delay_millis=500,
                )
            )
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=2),
                    export_interval_millis=1000,
                    export_timeout_millis=3000,
                )
            )
        if span_exporter is not None:
            self.traces.add_span_processor(SimpleSpanProcessor(span_exporter))
        if log_exporter is not None:
            self.logs.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
        if metric_reader is not None:
            readers.append(metric_reader)
        self.metrics = MeterProvider(
            resource=resource,
            metric_readers=readers,
            views=[
                View(
                    instrument_name="incidentiq.request.duration",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
                    ),
                )
            ],
        )
        self.tracer = self.traces.get_tracer("incidentiq.simulator")
        self.otel_logger = self.logs.get_logger("incidentiq.simulator")
        self.logger = logging.getLogger(f"incidentiq.simulator.{service}")
        meter = self.metrics.get_meter("incidentiq.simulator")
        self.duration = meter.create_histogram(
            "incidentiq.request.duration",
            unit="s",
            description="Synthetic service request duration",
        )
        self.pool_wait = meter.create_histogram(
            "incidentiq.database.pool.wait",
            unit="s",
            description="Synthetic inventory database pool acquisition wait",
        )

    def record_request(
        self,
        *,
        route: str,
        request_id: str,
        run_id: str | None,
        duration_seconds: float,
        status_code: int,
        revision: str | None = None,
        error_type: str | None = None,
    ) -> None:
        span = get_current_span().get_span_context()
        attributes = {"http.route": route, "http.response.status_code": status_code}
        if revision is not None:
            attributes["deployment.revision"] = revision
        # Run/request/trace IDs belong in logs and spans, never metric dimensions.
        self.duration.record(duration_seconds, attributes)
        fields = {
            "service": self.service,
            "request_id": request_id,
            "trace_id": f"{span.trace_id:032x}",
            "span_id": f"{span.span_id:016x}",
            "operation": f"POST {route}",
            "status_code": status_code,
            "latency_ms": round(duration_seconds * 1000, 3),
        }
        if run_id is not None:
            fields["run_id"] = run_id
        if revision is not None:
            fields["deployment.revision"] = revision
        if error_type is not None:
            fields["error.type"] = error_type
        level = logging.ERROR if status_code >= 500 else logging.INFO
        self.logger.log(level, "request_completed", extra=fields)
        self.otel_logger.emit(
            body="request_completed",
            timestamp=time_ns(),
            severity_number=SeverityNumber.ERROR if status_code >= 500 else SeverityNumber.INFO,
            severity_text="ERROR" if status_code >= 500 else "INFO",
            attributes=fields,
        )

    def record_deployment(self, event: DeploymentEvent) -> None:
        fields = {
            "service": self.service,
            "run_id": str(event.run_id),
            "event.name": "deployment",
            "deployment.id": str(event.id),
            "deployment.revision": event.revision,
            "deployment.previous_revision": event.previous_revision,
            "deployment.state": "succeeded",
        }
        self.logger.info("deployment_changed", extra=fields)
        self.otel_logger.emit(
            body="deployment_changed",
            timestamp=event.timestamp_unix_nano,
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            attributes=fields,
        )

    def shutdown(self) -> None:
        self.traces.shutdown()
        self.metrics.shutdown(timeout_millis=3000)
        self.logs.shutdown()
