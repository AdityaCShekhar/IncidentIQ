import logging
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from incidentiq.api.schemas import (
    ErrorResponse,
    EvidencePage,
    EvidenceResponse,
    EvidenceSeverity,
    EvidenceSource,
    IncidentCreate,
    IncidentResponse,
)
from incidentiq.core.errors import APIError
from incidentiq.db.repositories import IncidentRepository
from incidentiq.ingestion.schemas import (
    CollectionIssue,
    CollectionLimits,
    EvidenceBundle,
    SourceCoverage,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/incidents",
    tags=["incidents"],
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


DatabaseSession = Annotated[Session, Depends(get_session)]


def validate_bundle(bundle: EvidenceBundle, data: IncidentCreate, incident_id: UUID) -> None:
    if bundle.window != data.evidence_window() or bundle.incident_id != incident_id:
        raise ValueError("Collector returned a different incident window")
    if len(bundle.records) > 1000:
        raise ValueError("Collector exceeded record limit")
    for record in bundle.records:
        if record.incident_id != incident_id or record.service not in bundle.window.services:
            raise ValueError("Collector returned evidence outside incident scope")
        if not data.started_at <= record.timestamp < data.ended_at:
            raise ValueError("Collector returned evidence outside incident window")


@router.post("", response_model=IncidentResponse, status_code=201)
def create_incident(
    data: IncidentCreate, request: Request, response: Response, session: DatabaseSession
):
    repository = IncidentRepository(session)
    incident = repository.create(data)
    incident_id = incident.id
    response.headers["Location"] = f"/incidents/{incident_id}"
    try:
        bundle = request.app.state.collector(
            request.app.state.settings.telemetry_directory,
            data.evidence_window(),
            incident_id=incident_id,
            limits=CollectionLimits(),
        )
        validate_bundle(bundle, data, incident_id)
    except Exception as error:
        logger.error(
            "evidence_collection_failed",
            extra={"request_id": request.state.request_id, "error_type": type(error).__name__},
        )
        bundle = EvidenceBundle(
            incident_id=incident_id,
            window=data.evidence_window(),
            status="unavailable",
            limits=CollectionLimits(),
            records=[],
            coverage={source: SourceCoverage() for source in ("trace", "log", "metric")},
            issues=[CollectionIssue(code="collector_failed")],
        )
    try:
        return repository.save_bundle(incident_id, bundle)
    except SQLAlchemyError:
        raise APIError(
            503,
            "evidence_persistence_failed",
            "Incident created, but evidence could not be saved",
            incident_id=incident_id,
        ) from None


@router.get(
    "/{incident_id}", response_model=IncidentResponse, responses={404: {"model": ErrorResponse}}
)
def get_incident(incident_id: UUID, session: DatabaseSession):
    return IncidentRepository(session).get(incident_id)


@router.get(
    "/{incident_id}/evidence",
    response_model=EvidencePage,
    responses={404: {"model": ErrorResponse}},
)
def get_evidence(
    incident_id: UUID,
    session: DatabaseSession,
    service: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")] = None,
    source: EvidenceSource | None = None,
    severity: EvidenceSeverity | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
):
    records, total = IncidentRepository(session).evidence_page(
        incident_id,
        service=service,
        source=source,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return EvidencePage(
        incident_id=incident_id,
        items=[EvidenceResponse.model_validate(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(records) < total,
    )
