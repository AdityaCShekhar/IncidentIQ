from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from incidentiq.ingestion.schemas import (
    CollectionIssue,
    CollectionLimits,
    EvidenceRecord,
    EvidenceWindow,
    SourceCoverage,
    SourceType,
)

EvidenceSource = Literal["log", "trace", "metric", "deployment", "runbook", "prior_incident"]
EvidenceSeverity = Literal["trace", "debug", "info", "warning", "error", "critical"]


class IncidentCreate(EvidenceWindow):
    title: str = Field(min_length=1, max_length=200)
    severity: Literal["critical", "high", "medium", "low"]
    scenario_id: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("title", "description", "scenario_id")
    @classmethod
    def postgres_text(cls, value):
        if value is not None:
            if "\x00" in value:
                raise ValueError("NUL is not valid PostgreSQL text")
            value.encode("utf-8")
        return value

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    def evidence_window(self) -> EvidenceWindow:
        return EvidenceWindow.model_validate(
            self.model_dump(include={"service", "related_services", "started_at", "ended_at"})
        )


class CollectionSummary(BaseModel):
    status: Literal["collected", "partial", "empty", "unavailable"]
    limits: CollectionLimits
    coverage: dict[SourceType, SourceCoverage]
    issues: list[CollectionIssue]
    record_count: int = Field(ge=0, le=1000)


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    title: str
    service: str
    related_services: list[str]
    severity: Literal["critical", "high", "medium", "low"]
    description: str | None
    started_at: datetime
    ended_at: datetime
    scenario_id: str | None
    status: Literal["created", "collecting", "ready", "failed"]
    created_at: datetime
    evidence_count: int
    collection: CollectionSummary | None = Field(validation_alias="collection_metadata")

    @field_validator("collection", mode="before")
    @classmethod
    def empty_collection(cls, value):
        return value or None


class EvidenceResponse(EvidenceRecord):
    model_config = ConfigDict(from_attributes=True, extra="forbid", frozen=True)
    source_type: EvidenceSource


class EvidencePage(BaseModel):
    incident_id: UUID
    items: list[EvidenceResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["rules", "llm_summary", "rag", "agent"]
    model: str | None = Field(default=None, min_length=1, max_length=200)


class InvestigationResponse(BaseModel):
    id: UUID
    incident_id: UUID
    strategy: str
    status: str
    model_name: str | None
    model_configuration: dict = Field(validation_alias="model_config")
    result: dict | None
    error_code: str | None
    error_summary: str | None
    started_at: datetime
    completed_at: datetime | None
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    tool_calls: list[dict] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    incident_id: UUID | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
