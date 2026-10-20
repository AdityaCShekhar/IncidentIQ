"""Add curated knowledge chunks with full text and vector search."""

from alembic import op

revision = "0003_knowledge_chunks"
down_revision = "0002_persist_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """CREATE TABLE knowledge_chunks (
            id uuid PRIMARY KEY,
            document_id text NOT NULL,
            section text NOT NULL,
            text text NOT NULL,
            source text NOT NULL,
            document_type text NOT NULL CHECK (document_type IN ('runbook', 'prior_incident')),
            service varchar(100) NOT NULL,
            tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            content_hash varchar(64) NOT NULL,
            embedding_model varchar(100) NOT NULL,
            embedding vector(384) NOT NULL,
            search_vector tsvector GENERATED ALWAYS AS
                (to_tsvector('simple', coalesce(section, '') || ' ' || text)) STORED,
            UNIQUE (document_id, section, content_hash)
        )"""
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_search ON knowledge_chunks USING gin(search_vector)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE knowledge_chunks")
    op.execute("DROP EXTENSION vector")
