"""Create the initial incident table."""

import sqlalchemy as sa
from alembic import op

revision = "0001_create_incidents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("service", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scenario_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), server_default="created", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("ended_at > started_at", name="ck_incidents_valid_window"),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')", name="ck_incidents_severity"
        ),
        sa.CheckConstraint(
            "status IN ('created', 'collecting', 'ready', 'failed')", name="ck_incidents_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incidents_service", "incidents", ["service"])


def downgrade() -> None:
    op.drop_index("ix_incidents_service", table_name="incidents")
    op.drop_table("incidents")
