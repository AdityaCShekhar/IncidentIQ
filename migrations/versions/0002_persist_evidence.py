"""Persist evidence records and incident collection status."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_persist_evidence"
down_revision = "0001_create_incidents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("related_services", postgresql.JSONB(), server_default="[]", nullable=False),
    )
    op.add_column(
        "incidents", sa.Column("evidence_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("incidents", sa.Column("collection_status", sa.String(20), nullable=True))
    op.add_column(
        "incidents",
        sa.Column("collection_metadata", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_check_constraint("ck_incidents_evidence_count", "incidents", "evidence_count >= 0")
    op.create_check_constraint(
        "ck_incidents_collection_status",
        "incidents",
        "collection_status IN ('collected', 'partial', 'empty', 'unavailable')",
    )
    op.create_table(
        "evidence_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("service", sa.String(100), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("raw_locator", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "content_hash", name="uq_evidence_incident_content"),
        sa.CheckConstraint(
            "source_type IN ('log', 'trace', 'metric', 'deployment', 'runbook', 'prior_incident')",
            name="ck_evidence_source_type",
        ),
        sa.CheckConstraint(
            "severity IN ('trace', 'debug', 'info', 'warning', 'error', 'critical')",
            name="ck_evidence_severity",
        ),
    )
    op.create_index(
        "ix_evidence_incident_timestamp_id", "evidence_records", ["incident_id", "timestamp", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_incident_timestamp_id", table_name="evidence_records")
    op.drop_table("evidence_records")
    op.drop_constraint("ck_incidents_collection_status", "incidents", type_="check")
    op.drop_constraint("ck_incidents_evidence_count", "incidents", type_="check")
    for column in (
        "collection_metadata",
        "collection_status",
        "evidence_count",
        "related_services",
    ):
        op.drop_column("incidents", column)
