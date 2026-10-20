import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from incidentiq.db.types import UTCDateTime

JSON_DATA = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("ended_at > started_at", name="ck_incidents_valid_window"),
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')", name="ck_incidents_severity"
        ),
        CheckConstraint(
            "status IN ('created', 'collecting', 'ready', 'failed')", name="ck_incidents_status"
        ),
        CheckConstraint("evidence_count >= 0", name="ck_incidents_evidence_count"),
        CheckConstraint(
            "collection_status IN ('collected', 'partial', 'empty', 'unavailable')",
            name="ck_incidents_collection_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    service: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime())
    scenario_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), server_default="created")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    related_services: Mapped[list[str]] = mapped_column(
        JSON_DATA, default=list, server_default="[]"
    )
    evidence_count: Mapped[int] = mapped_column(Integer, server_default="0")
    collection_status: Mapped[str | None] = mapped_column(String(20))
    collection_metadata: Mapped[dict] = mapped_column(JSON_DATA, default=dict, server_default="{}")


class EvidenceRow(Base):
    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint("incident_id", "content_hash", name="uq_evidence_incident_content"),
        CheckConstraint(
            "source_type IN ('log', 'trace', 'metric', 'deployment', 'runbook', 'prior_incident')",
            name="ck_evidence_source_type",
        ),
        CheckConstraint(
            "severity IN ('trace', 'debug', 'info', 'warning', 'error', 'critical')",
            name="ck_evidence_severity",
        ),
        Index("ix_evidence_incident_timestamp_id", "incident_id", "timestamp", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(20))
    service: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime())
    severity: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON_DATA)
    source_uri: Mapped[str] = mapped_column(Text)
    raw_locator: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        CheckConstraint("strategy IN ('rules', 'llm_summary', 'rag', 'agent')", name="ck_investigations_strategy"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_investigations_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="running")
    model_name: Mapped[str | None] = mapped_column(String(200))
    model_config: Mapped[dict] = mapped_column(JSON_DATA, default=dict, server_default="{}")
    result: Mapped[dict | None] = mapped_column(JSON_DATA)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latency_ms: Mapped[float | None] = mapped_column()
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON_DATA)
    output_summary: Mapped[dict] = mapped_column(JSON_DATA)
    status: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[float] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(64))
