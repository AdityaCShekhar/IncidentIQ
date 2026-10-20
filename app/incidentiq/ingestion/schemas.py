from datetime import UTC, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

SourceType = Literal["trace", "log", "metric", "deployment"]
ServiceName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]


class EvidenceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: ServiceName
    related_services: tuple[ServiceName, ...] = Field(default=(), max_length=8)
    started_at: AwareDatetime
    ended_at: AwareDatetime

    @field_validator("started_at", "ended_at")
    @classmethod
    def normalize_utc(cls, value):
        return value.astimezone(UTC)

    @field_validator("related_services")
    @classmethod
    def sort_services(cls, value):
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_window(self):
        duration = self.ended_at - self.started_at
        if not timedelta(0) < duration <= timedelta(hours=1):
            raise ValueError("Incident window must be positive and no longer than one hour")
        return self

    @property
    def services(self) -> set[str]:
        return {self.service, *self.related_services}


class CollectionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=16, ge=1, le=64)
    max_scan_bytes: int = Field(default=32 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)
    max_line_bytes: int = Field(default=1024 * 1024, ge=1, le=1024 * 1024)
    max_lines: int = Field(default=100_000, ge=1, le=100_000)
    max_observations: int = Field(default=25_000, ge=1, le=50_000)
    max_records: int = Field(default=1000, ge=1, le=1000)
    deadline_seconds: float = Field(default=5, gt=0, le=30)


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: UUID
    incident_id: UUID
    source_type: SourceType
    service: ServiceName
    timestamp: AwareDatetime
    severity: Literal["trace", "debug", "info", "warning", "error", "critical"]
    summary: str = Field(max_length=1000)
    payload: dict[str, JsonValue]
    source_uri: str
    raw_locator: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceCoverage(BaseModel):
    files_scanned: int = 0
    bytes_scanned: int = 0
    lines_scanned: int = 0
    observations_scanned: int = 0
    records_collected: int = 0
    duplicates: int = 0


class CollectionIssue(BaseModel):
    code: str
    source_type: SourceType | None = None
    count: int = 1


class EvidenceBundle(BaseModel):
    schema_version: Literal[1] = 1
    incident_id: UUID
    window: EvidenceWindow
    status: Literal["collected", "partial", "empty", "unavailable"]
    limits: CollectionLimits
    records: list[EvidenceRecord]
    coverage: dict[SourceType, SourceCoverage]
    issues: list[CollectionIssue]
