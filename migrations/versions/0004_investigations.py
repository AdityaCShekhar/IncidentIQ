"""Persist bounded investigation runs and tool audits."""

from alembic import op

revision = "0004_investigations"
down_revision = "0003_knowledge_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE investigations (
            id uuid PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            strategy varchar(20) NOT NULL CHECK (strategy IN ('rules', 'llm_summary', 'rag', 'agent')),
            status varchar(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
            model_name varchar(200), model_config jsonb NOT NULL DEFAULT '{}'::jsonb,
            result jsonb, error_code varchar(64), error_summary varchar(500),
            started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
            latency_ms double precision, prompt_tokens integer, completion_tokens integer
        )"""
    )
    op.execute("CREATE INDEX ix_investigations_incident_id ON investigations(incident_id)")
    op.execute(
        """CREATE TABLE tool_calls (
            id uuid PRIMARY KEY,
            investigation_id uuid NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            tool_name varchar(64) NOT NULL, input jsonb NOT NULL,
            output_summary jsonb NOT NULL, status varchar(20) NOT NULL,
            latency_ms double precision NOT NULL, error_code varchar(64)
        )"""
    )
    op.execute("CREATE INDEX ix_tool_calls_investigation_id ON tool_calls(investigation_id)")


def downgrade() -> None:
    op.execute("DROP TABLE tool_calls")
    op.execute("DROP TABLE investigations")
