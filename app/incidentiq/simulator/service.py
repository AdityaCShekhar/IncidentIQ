import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from incidentiq.core.logging import configure_logging
from incidentiq.simulator.config import SimulatorSettings
from incidentiq.simulator.deployments import (
    DeploymentCapacityError,
    DeploymentRegistry,
    DeploymentRequest,
    DeploymentResult,
)
from incidentiq.simulator.telemetry import Telemetry


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    delay_ms: int = Field(default=20, ge=0, le=2000, strict=True)
    fault_mode: Literal["none", "pool_exhaustion", "retry_storm"] = "none"


class Reservation(BaseModel):
    service: str = Field(pattern="^inventory$")
    status: str = Field(pattern="^reserved$")
    run_id: UUID


def create_app(
    settings: SimulatorSettings | None = None,
    *,
    telemetry: Telemetry | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    deployments: DeploymentRegistry | None = None,
) -> FastAPI:
    settings = settings or SimulatorSettings()
    propagator = TraceContextTextMapPropagator()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging("INFO")
        runtime = telemetry or Telemetry(
            settings.role,
            str(settings.otlp_endpoint) if settings.otlp_endpoint else None,
        )
        application.state.telemetry = runtime
        application.state.deployments = deployments or DeploymentRegistry()
        application.state.database_slots = asyncio.Semaphore(2)
        try:
            async with httpx.AsyncClient(
                base_url=str(settings.inventory_url),
                timeout=settings.dependency_timeout_seconds,
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            ) as client:
                application.state.client = client
                yield
        finally:
            runtime.shutdown()

    application = FastAPI(title=f"IncidentIQ simulator: {settings.role}", lifespan=lifespan)

    @application.middleware("http")
    async def observe(request: Request, call_next):
        try:
            request_id = str(UUID(request.headers.get("X-Request-ID", "")))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        route = "/checkout" if settings.role == "checkout" else "/reserve"
        if request.method != "POST" or request.url.path != route:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        runtime = request.app.state.telemetry
        parent = propagator.extract(dict(request.headers))
        started = perf_counter()
        with runtime.tracer.start_as_current_span(
            f"POST {route}",
            context=parent,
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": "POST",
                "http.route": route,
                "request_id": request_id,
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                response = await call_next(request)
            except Exception:
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "internal_error",
                            "message": "Internal server error",
                            "request_id": request_id,
                        }
                    },
                )
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(StatusCode.ERROR)
            run_id = getattr(request.state, "run_id", None)
            if run_id is not None:
                span.set_attribute("incidentiq.run_id", run_id)
            revision = getattr(request.state, "revision", None)
            error_type = getattr(request.state, "error_type", None)
            if revision is not None:
                span.set_attribute("deployment.revision", revision)
                response.headers["X-Simulator-Revision"] = revision
            retry_attempts = getattr(request.state, "retry_attempts", None)
            if retry_attempts is not None:
                response.headers["X-Simulator-Retry-Attempts"] = str(retry_attempts)
            if error_type is not None:
                span.set_attribute("error.type", error_type)
            runtime.record_request(
                route=route,
                request_id=request_id,
                run_id=run_id,
                duration_seconds=perf_counter() - started,
                status_code=response.status_code,
                revision=revision,
                error_type=error_type,
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = f"{span.get_span_context().trace_id:032x}"
            return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": f"http_{error.status_code}",
                    "message": str(error.detail),
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid simulator request",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "service": settings.role}

    if settings.role == "inventory":

        @application.post("/reserve", response_model=Reservation)
        async def reserve(payload: SimulationRequest, request: Request) -> Reservation:
            request.state.run_id = str(payload.run_id)
            if payload.fault_mode == "pool_exhaustion":
                started = perf_counter()
                try:
                    await asyncio.wait_for(request.app.state.database_slots.acquire(), timeout=0.02)
                except TimeoutError:
                    request.state.error_type = "database_pool_exhausted"
                    request.app.state.telemetry.pool_wait.record(
                        perf_counter() - started, {"service": "inventory", "result": "timeout"}
                    )
                    return JSONResponse(
                        status_code=503,
                        content={"error": {"code": "database_pool_exhausted"}},
                    )
                request.app.state.telemetry.pool_wait.record(
                    perf_counter() - started, {"service": "inventory", "result": "acquired"}
                )
                try:
                    await asyncio.sleep(0.25)
                finally:
                    request.app.state.database_slots.release()
            if payload.fault_mode == "retry_storm":
                request.state.error_type = "downstream_unavailable"
                await asyncio.sleep(0.1)
                return JSONResponse(
                    status_code=503,
                    content={"error": {"code": "synthetic_unavailable"}},
                )
            await asyncio.sleep(payload.delay_ms / 1000)
            return Reservation(service="inventory", status="reserved", run_id=payload.run_id)
    else:

        @application.post("/simulator/deployments", response_model=DeploymentResult)
        async def deploy(payload: DeploymentRequest, request: Request) -> DeploymentResult:
            try:
                result = request.app.state.deployments.activate(payload)
            except DeploymentCapacityError:
                raise HTTPException(429, "Active simulator deployment limit reached") from None
            if result.changed:
                request.app.state.telemetry.record_deployment(result.event)
            return result

        @application.post("/checkout")
        async def checkout(payload: SimulationRequest, request: Request) -> dict[str, str]:
            request.state.run_id = str(payload.run_id)
            revision = request.app.state.deployments.revision(payload.run_id)
            request.state.revision = revision
            runtime = request.app.state.telemetry
            attempts = 3 if payload.fault_mode == "retry_storm" else 1
            request.state.retry_attempts = attempts
            reservation = None
            failure = None
            for attempt in range(attempts):
                with runtime.tracer.start_as_current_span(
                    "POST inventory /reserve",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "peer.service": "inventory",
                        "http.request.method": "POST",
                        "incidentiq.run_id": str(payload.run_id),
                        "deployment.revision": revision,
                        "incidentiq.retry_attempt": attempt,
                    },
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span:
                    headers = {"X-Request-ID": request.state.request_id}
                    propagator.inject(headers)
                    try:
                        response = await request.app.state.client.post(
                            "/reserve",
                            json=payload.model_dump(mode="json"),
                            headers=headers,
                        )
                        span.set_attribute("http.response.status_code", response.status_code)
                        if response.status_code != 200:
                            failure = "unavailable"
                            span.set_status(StatusCode.ERROR)
                            span.set_attribute("error.type", failure)
                        else:
                            reservation = Reservation.model_validate_json(response.content)
                            if reservation.run_id != payload.run_id:
                                raise ValueError("Downstream run mismatch")
                            failure = None
                    except httpx.TimeoutException:
                        failure = "timeout"
                        span.set_status(StatusCode.ERROR)
                        span.set_attribute("error.type", failure)
                    except httpx.RequestError:
                        failure = "unavailable"
                        span.set_status(StatusCode.ERROR)
                        span.set_attribute("error.type", failure)
                    except (ValueError, ValidationError):
                        failure = "invalid_response"
                        span.set_status(StatusCode.ERROR)
                        span.set_attribute("error.type", failure)
                if reservation is not None:
                    break
            if failure is not None:
                if failure == "timeout":
                    raise HTTPException(
                        status_code=504, detail="Inventory request timed out"
                    ) from None
                if failure == "unavailable":
                    raise HTTPException(status_code=503, detail="Inventory unavailable") from None
                raise HTTPException(status_code=502, detail="Invalid inventory response") from None
            if revision == "checkout-v2":
                # Reproduce a response formatter regression after a healthy dependency call.
                # The new formatter expects a field absent from inventory's current contract.
                try:
                    reservation.model_dump()["reservation_id"]
                except KeyError:
                    request.state.error_type = "response_contract_error"
                    return JSONResponse(
                        status_code=500,
                        content={
                            "error": {
                                "code": "response_contract_error",
                                "message": "Checkout response contract mismatch",
                                "request_id": request.state.request_id,
                            }
                        },
                    )
            return {"service": "checkout", "status": "completed", "run_id": str(payload.run_id)}

    return application


app = create_app()
