from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentiq.agent.ollama import OllamaAdapter
from incidentiq.agent.workflows import WorkflowError, run_agent, run_rag
from incidentiq.api.routes_incidents import DatabaseSession
from incidentiq.api.schemas import ErrorResponse, InvestigationCreate, InvestigationResponse
from incidentiq.baselines.rules import run_rules
from incidentiq.core.errors import APIError
from incidentiq.db.models import EvidenceRow, Incident, Investigation, ToolCall
from incidentiq.ingestion.schemas import (
    CollectionIssue, CollectionLimits, EvidenceBundle, EvidenceRecord, EvidenceWindow, SourceCoverage,
)
from incidentiq.retrieval.knowledge import Retriever, load_corpus

router = APIRouter(tags=["investigations"])


@lru_cache(maxsize=1)
def _retriever() -> Retriever:
    return Retriever(load_corpus(Path("knowledge")))


def _response(session: Session, run: Investigation) -> InvestigationResponse:
    calls = session.scalars(select(ToolCall).where(ToolCall.investigation_id == run.id)).all()
    return InvestigationResponse(
        id=run.id, incident_id=run.incident_id, strategy=run.strategy, status=run.status,
        model_name=run.model_name, model_configuration=run.model_config, result=run.result,
        error_code=run.error_code, error_summary=run.error_summary, started_at=run.started_at,
        completed_at=run.completed_at, latency_ms=run.latency_ms,
        prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens,
        tool_calls=[{"tool_name": c.tool_name, "input": c.input, "output_summary": c.output_summary,
                     "status": c.status, "latency_ms": c.latency_ms, "error_code": c.error_code} for c in calls],
    )


@router.post("/incidents/{incident_id}/investigations", response_model=InvestigationResponse,
             status_code=201, responses={404: {"model": ErrorResponse}})
def create_investigation(incident_id: UUID, data: InvestigationCreate, session: DatabaseSession):
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise APIError(404, "incident_not_found", "Incident not found")
    rows = session.scalars(select(EvidenceRow).where(EvidenceRow.incident_id == incident_id)
                           .order_by(EvidenceRow.timestamp, EvidenceRow.id).limit(1000)).all()
    collection = incident.collection_metadata or {}
    window = EvidenceWindow(service=incident.service, related_services=incident.related_services,
                            started_at=incident.started_at, ended_at=incident.ended_at)
    coverage_data = collection.get("coverage", {})
    bundle = EvidenceBundle(
        incident_id=incident_id, window=window,
        status=collection.get("status", incident.collection_status or "empty"),
        limits=collection.get("limits", CollectionLimits().model_dump(mode="json")),
        records=[EvidenceRecord.model_validate(row) for row in rows],
        coverage={key: SourceCoverage.model_validate(value) for key, value in coverage_data.items()},
        issues=[CollectionIssue.model_validate(issue) for issue in collection.get("issues", [])],
    )
    if data.strategy != "rules" and not data.model:
        raise APIError(422, "model_required", "A local model name is required for this strategy")
    run = Investigation(id=uuid4(), incident_id=incident_id, strategy=data.strategy,
                        status="running", model_name=data.model, model_config={})
    session.add(run)
    session.commit()
    started = monotonic()
    try:
        if data.strategy == "rules":
            result = run_rules(bundle)
            report, metadata, audits = result, {"provider": "deterministic", "calls": 0}, ()
        else:
            adapter = OllamaAdapter(data.model)
            if data.strategy == "llm_summary":
                model_result = adapter.diagnose(bundle)
                report, metadata, audits = model_result.report, model_result.metadata, ()
            elif data.strategy == "rag":
                workflow = run_rag(bundle, adapter, _retriever())
                report, metadata, audits = workflow.report, workflow.metadata, workflow.tool_audits
            else:
                workflow = run_agent(bundle, adapter, _retriever())
                report, metadata, audits = workflow.report, workflow.metadata, workflow.tool_audits
        run.status = "completed"
        run.result = {"report": report.model_dump(mode="json"), "metadata": metadata}
        run.model_config = metadata
        run.prompt_tokens = metadata.get("prompt_tokens")
        run.completion_tokens = metadata.get("completion_tokens")
    except Exception as error:
        safe_codes = {"model_timeout", "model_unavailable", "model_response_too_large",
                      "invalid_model_response", "invalid_model_output", "agent_planning_failed",
                      "agent_diagnosis_failed"}
        code = str(error) if str(error) in safe_codes else "investigation_failed"
        run.status, run.error_code, run.error_summary = "failed", code, "Investigation could not be completed"
        audits = error.tool_audits if isinstance(error, WorkflowError) else ()
    run.completed_at = datetime.now(UTC)
    run.latency_ms = round((monotonic() - started) * 1000, 3)
    for audit in audits:
        session.add(ToolCall(investigation_id=run.id, tool_name=audit.tool_name,
                             input=audit.input, output_summary=audit.output_summary,
                             status=audit.status, latency_ms=audit.latency_ms, error_code=audit.error_code))
    session.commit()
    return _response(session, run)


@router.get("/investigations/{investigation_id}", response_model=InvestigationResponse,
            responses={404: {"model": ErrorResponse}})
def get_investigation(investigation_id: UUID, session: DatabaseSession):
    run = session.get(Investigation, investigation_id)
    if run is None:
        raise APIError(404, "investigation_not_found", "Investigation not found")
    return _response(session, run)
