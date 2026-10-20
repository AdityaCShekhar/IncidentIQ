"""PostgreSQL persistence metadata for the retrieval index."""

from uuid import UUID

from sqlalchemy import CheckConstraint, Computed, Index, String, Text, UniqueConstraint, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class RetrievalBase(DeclarativeBase):
    """Separate metadata keeps PostgreSQL-only retrieval types out of SQLite tests."""


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "vector(384)"


class KnowledgeChunk(RetrievalBase):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('runbook', 'prior_incident')",
            name="knowledge_chunks_document_type_check",
        ),
        UniqueConstraint("document_id", "section", "content_hash"),
        Index("ix_knowledge_chunks_search", "search_vector", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(Text)
    service: Mapped[str] = mapped_column(String(100))
    tags: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"))
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding: Mapped[list[float]] = mapped_column(Vector())
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('simple', coalesce(section, '') || ' ' || text)", persisted=True),
    )
