import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from starlette.exceptions import HTTPException

from incidentiq.api.routes_incidents import router as incidents_router
from incidentiq.core.config import Settings
from incidentiq.core.errors import APIError
from incidentiq.core.logging import configure_logging
from incidentiq.db.session import build_engine, database_is_ready
from incidentiq.ingestion.telemetry import collect_evidence

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, collector=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        engine = build_engine(settings)
        application.state.engine = engine
        application.state.settings = settings
        application.state.collector = collector or collect_evidence
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="IncidentIQ",
        description="Incident investigation copilot with persistent telemetry evidence.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        try:
            request_id = str(UUID(request.headers.get("X-Request-ID", "")))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error(
                "request_failed",
                extra={"request_id": request_id, "error_type": type(error).__name__},
            )
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
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "status_code": response.status_code,
                "latency_ms": round((perf_counter() - start) * 1000, 2),
            },
        )
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            headers=error.headers,
            content={
                "error": {
                    "code": f"http_{error.status_code}",
                    "message": str(error.detail),
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(APIError)
    async def application_error(request: Request, error: APIError) -> JSONResponse:
        detail = {
            "code": error.code,
            "message": error.message,
            "request_id": request.state.request_id,
        }
        if error.incident_id is not None:
            detail["incident_id"] = str(error.incident_id)
        return JSONResponse(status_code=error.status_code, content={"error": detail})

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        timed_out = isinstance(error, DatabaseTimeout) or getattr(
            getattr(error, "orig", None), "pgcode", None
        ) in {"57014", "55P03"}
        logger.warning(
            "database_request_failed",
            extra={"request_id": request.state.request_id, "error_type": type(error).__name__},
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "database_timeout" if timed_out else "database_unavailable",
                    "message": "Database request timed out"
                    if timed_out
                    else "Database unavailable",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok", "service": "incidentiq-api"}

    @application.get(
        "/health/ready",
        tags=["health"],
        responses={503: {"description": "Database or schema unavailable"}},
    )
    def ready(request: Request) -> JSONResponse:
        if not database_is_ready(request.app.state.engine):
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "database_not_ready",
                        "message": "Database unavailable or migrations not applied",
                        "request_id": request.state.request_id,
                    }
                },
            )
        return JSONResponse({"status": "ready", "checks": {"database": "ok", "schema": "ok"}})

    application.include_router(incidents_router)
    return application


app = create_app()
