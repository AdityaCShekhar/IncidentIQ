from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

if TYPE_CHECKING:
    from sqlalchemy import Engine

TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
DIMENSIONS = 384
HASH_MODEL = "hash-ngram-v1"
CORPUS_VERSION = "knowledge-v1"


@dataclass(frozen=True)
class Chunk:
    id: UUID
    document_id: str
    section: str
    text: str
    source: str
    document_type: str
    service: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int
    method: str


def _chunk_tags(path: Path, section: str) -> tuple[str, ...]:
    if path.name == "checkout.md":
        if section == "Slow dependency requests":
            return ("slow_dependency", "inventory")
        if section == "Checkout database pool":
            return ("database_pool_exhaustion", "pool")
        if section == "Failed checkout after deployment":
            return ("deployment_regression", "revision")
    return {
        "database": ("database_pool_exhaustion", "pool", "database"),
        "retry-handling": ("retry_storm", "retry", "inventory"),
        "deployment": ("deployment_regression", "deployment", "revision"),
        "slow-inventory": ("slow_dependency", "inventory"),
        "checkout-revision": ("deployment_regression", "revision"),
        "pool-pressure": ("database_pool_exhaustion", "pool"),
        "retry-amplification": ("retry_storm", "retry"),
    }.get(path.stem, ())


def _chunks(path: Path, root: Path) -> list[Chunk]:
    source = path.relative_to(root).as_posix()
    document_id = source.removesuffix(".md")
    document_type = "runbook" if source.startswith("runbooks/") else "prior_incident"
    service = "inventory" if "inventory" in path.stem else "checkout"
    content = path.read_text(encoding="utf-8")
    heading = ""
    body: list[str] = []
    result = []

    def flush() -> None:
        text = " ".join(line.strip() for line in body if line.strip()).strip()
        if not text:
            return
        identity = f"incidentiq:knowledge:{CORPUS_VERSION}:{document_id}:{heading}:{text}"
        result.append(
            Chunk(
                id=uuid5(NAMESPACE_URL, identity),
                document_id=document_id,
                section=heading or "Overview",
                text=text,
                source=source,
                document_type=document_type,
                service=service,
                tags=_chunk_tags(path, heading),
            )
        )

    for line in content.splitlines():
        if line.startswith("## "):
            flush()
            heading, body = line[3:].strip(), []
        elif line.startswith("# "):
            if not heading:
                heading = line[2:].strip()
        else:
            body.append(line)
    flush()
    return result


def load_corpus(root: Path) -> list[Chunk]:
    root = root.resolve()
    files = sorted(root.rglob("*.md"))
    if not files or len(files) > 100:
        raise ValueError("Knowledge corpus must contain between one and 100 documents")
    if any(path.is_symlink() or not path.resolve().is_relative_to(root) for path in files):
        raise ValueError("Knowledge path escapes its corpus root")
    if any(path.stat().st_size > 256 * 1024 for path in files):
        raise ValueError("Knowledge document exceeds its size limit")
    chunks = [chunk for path in files for chunk in _chunks(path, root)]
    if not chunks or len(chunks) > 1000:
        raise ValueError("Knowledge corpus must contain between one and 1000 semantic chunks")
    if len({chunk.id for chunk in chunks}) != len(chunks):
        raise ValueError("Knowledge chunk IDs must be unique")
    return chunks


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN.findall(text)]


def embed(text: str) -> tuple[float, ...]:
    """Create a deterministic, local hashed token and character-ngram feature vector."""
    vector = [0.0] * DIMENSIONS
    tokens = _tokens(text)
    features = [(token, 1.0) for token in tokens]
    for token in tokens:
        padded = f"^{token}$"
        features.extend((padded[index : index + 3], 0.25) for index in range(len(padded) - 2))
    for feature, weight in features:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        sign = 1 if digest[4] & 1 else -1
        vector[index] += weight * sign
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector) if norm else tuple(vector)


class HashNgramEmbedder:
    model_name = HASH_MODEL
    library_version = "builtin-v1"

    def documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [embed(value) for value in texts]

    def query(self, value: str) -> tuple[float, ...]:
        return embed(value)


class FastEmbedModel:
    """Locally execute the quantized BGE model through FastEmbed/ONNX Runtime."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        try:
            from fastembed import TextEmbedding
        except ImportError as error:
            raise ValueError(
                "Install the retrieval extra with `uv sync --extra retrieval` to use local "
                "model embeddings"
            ) from error
        if model_name != "BAAI/bge-small-en-v1.5":
            raise ValueError("Unsupported local embedding model")
        self.model_name = model_name
        self.library_version = version("fastembed")
        self.model = TextEmbedding(model_name=model_name)

    def documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        vectors = self.model.embed([f"passage: {value}" for value in texts])
        result = [tuple(float(value) for value in vector) for vector in vectors]
        if any(len(vector) != DIMENSIONS for vector in result):
            raise ValueError("Local embedding dimension does not match the pgvector schema")
        return result

    def query(self, value: str) -> tuple[float, ...]:
        vector = next(self.model.query_embed(f"query: {value}"))
        result = tuple(float(item) for item in vector)
        if len(result) != DIMENSIONS:
            raise ValueError("Local embedding dimension does not match the pgvector schema")
        return result


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class Retriever:
    def __init__(self, chunks: list[Chunk], embedder=None):
        self.chunks = chunks
        self.embedder = embedder or HashNgramEmbedder()
        self.tokens = {chunk.id: Counter(_tokens(chunk.text)) for chunk in chunks}
        vectors = self.embedder.documents([chunk.text for chunk in chunks])
        self.embeddings = {chunk.id: vector for chunk, vector in zip(chunks, vectors, strict=True)}
        self.average_length = (
            sum(sum(counts.values()) for counts in self.tokens.values()) / len(chunks)
            if chunks
            else 0
        )
        document_frequency = Counter()
        for counts in self.tokens.values():
            document_frequency.update(counts.keys())
        self.idf = {
            term: math.log(1 + (len(chunks) - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def lexical(self, query: str, limit: int = 10, *, per_document: int = 3) -> list[Hit]:
        terms = _tokens(query)
        scored = []
        for chunk in self.chunks:
            counts = self.tokens[chunk.id]
            length = sum(counts.values())
            score = 0.0
            for term in terms:
                frequency = counts[term]
                if frequency:
                    denominator = frequency + 1.2 * (
                        0.25 + 0.75 * length / max(1, self.average_length)
                    )
                    score += self.idf.get(term, 0) * frequency * 2.2 / denominator
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], str(item[1].id)))
        return _cap_results(scored, limit, per_document, "lexical")

    def vector(self, query: str, limit: int = 10, *, per_document: int = 3) -> list[Hit]:
        query_vector = self.embedder.query(query)
        scored = [
            (_cosine(query_vector, self.embeddings[chunk.id]), chunk) for chunk in self.chunks
        ]
        scored.sort(key=lambda item: (-item[0], str(item[1].id)))
        return _cap_results(scored, limit, per_document, "vector")

    def hybrid(self, query: str, limit: int = 10, *, per_document: int = 3) -> list[Hit]:
        lexical, vector = self.lexical(query, limit * 3), self.vector(query, limit * 3)
        scores: dict[UUID, float] = {}
        by_id = {}
        for hit in (*lexical, *vector):
            by_id[hit.chunk.id] = hit.chunk
            scores[hit.chunk.id] = scores.get(hit.chunk.id, 0.0) + 1 / (60 + hit.rank)
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], str(chunk_id)))
        counts: Counter[str] = Counter()
        result = []
        for chunk_id in ordered:
            chunk = by_id[chunk_id]
            if counts[chunk.document_id] >= per_document:
                continue
            counts[chunk.document_id] += 1
            result.append(Hit(chunk, scores[chunk_id], len(result) + 1, "hybrid"))
            if len(result) == limit:
                break
        return result


class PostgresKnowledgeStore:
    """PostgreSQL full-text and pgvector backend for the local feature embeddings."""

    def __init__(self, engine: Engine, embedder=None):
        self.engine = engine
        self.chunks: list[Chunk] = []
        self.embedder = embedder or HashNgramEmbedder()

    def reindex(self, chunks: list[Chunk]) -> None:
        from sqlalchemy import text

        self.chunks = chunks
        statement = text(
            """INSERT INTO knowledge_chunks
               (id, document_id, section, text, source, document_type, service, tags,
                content_hash, embedding_model, embedding)
               VALUES (:id, :document_id, :section, :text, :source, :document_type, :service,
                       CAST(:tags AS jsonb), :content_hash, :embedding_model,
                       CAST(:embedding AS vector))"""
        )
        vectors = self.embedder.documents([chunk.text for chunk in chunks])
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM knowledge_chunks"))
            for chunk, vector in zip(chunks, vectors, strict=True):
                connection.execute(
                    statement,
                    {
                        "id": chunk.id,
                        "document_id": chunk.document_id,
                        "section": chunk.section,
                        "text": chunk.text,
                        "source": chunk.source,
                        "document_type": chunk.document_type,
                        "service": chunk.service,
                        "tags": json.dumps(chunk.tags),
                        "content_hash": hashlib.sha256(chunk.text.encode()).hexdigest(),
                        "embedding_model": self.embedder.model_name,
                        "embedding": "[" + ",".join(map(str, vector)) + "]",
                    },
                )

    def _query(self, sql: str, params: dict, method: str) -> list[Hit]:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            rows = connection.execute(text(sql), params).mappings().all()
        return [
            Hit(
                Chunk(
                    id=row["id"],
                    document_id=row["document_id"],
                    section=row["section"],
                    text=row["text"],
                    source=row["source"],
                    document_type=row["document_type"],
                    service=row["service"],
                    tags=tuple(row["tags"]),
                ),
                float(row["score"]),
                rank,
                method,
            )
            for rank, row in enumerate(rows, 1)
        ]

    def lexical(self, query: str, limit: int = 10) -> list[Hit]:
        hits = self._query(
            """WITH query_terms AS (
                   SELECT string_agg(quote_literal(term), ' | ') AS query
                   FROM regexp_split_to_table(lower(:query), '[^[:alnum:]_]+') AS term
                   WHERE term <> ''
               )
               SELECT id, document_id, section, text, source, document_type, service, tags,
                      ts_rank_cd(search_vector, to_tsquery('simple', query_terms.query)) AS score
               FROM knowledge_chunks
               CROSS JOIN query_terms
               WHERE query_terms.query IS NOT NULL
                 AND search_vector @@ to_tsquery('simple', query_terms.query)
               ORDER BY score DESC, id LIMIT :limit""",
            {"query": query[:2000], "limit": limit},
            "lexical",
        )
        return _cap_hits(hits, limit, 3)

    def vector(self, query: str, limit: int = 10) -> list[Hit]:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            indexed_models = set(
                connection.execute(text("SELECT DISTINCT embedding_model FROM knowledge_chunks"))
                .scalars()
                .all()
            )
        if indexed_models and indexed_models != {self.embedder.model_name}:
            raise ValueError("Indexed embeddings use another model; rebuild the knowledge index")
        vector = "[" + ",".join(map(str, self.embedder.query(query))) + "]"
        hits = self._query(
            """SELECT id, document_id, section, text, source, document_type, service, tags,
                      1 - (embedding <=> CAST(:embedding AS vector)) AS score
               FROM knowledge_chunks ORDER BY embedding <=> CAST(:embedding AS vector), id
               LIMIT :limit""",
            {"embedding": vector, "limit": limit},
            "vector",
        )
        return _cap_hits(hits, limit, 3)

    def hybrid(self, query: str, limit: int = 10, *, per_document: int = 3) -> list[Hit]:
        lexical, vector = self.lexical(query, limit * 3), self.vector(query, limit * 3)
        scores: dict[UUID, float] = {}
        chunks = {}
        for hit in (*lexical, *vector):
            chunks[hit.chunk.id] = hit.chunk
            scores[hit.chunk.id] = scores.get(hit.chunk.id, 0) + 1 / (60 + hit.rank)
        ordered = sorted(scores, key=lambda item: (-scores[item], str(item)))
        counts: Counter[str] = Counter()
        result = []
        for chunk_id in ordered:
            chunk = chunks[chunk_id]
            if counts[chunk.document_id] >= per_document:
                continue
            counts[chunk.document_id] += 1
            result.append(Hit(chunk, scores[chunk_id], len(result) + 1, "hybrid"))
            if len(result) == limit:
                break
        return result


def _cap_results(
    scored: list[tuple[float, Chunk]], limit: int, per_document: int, method: str
) -> list[Hit]:
    counts: Counter[str] = Counter()
    result = []
    for score, chunk in scored:
        if counts[chunk.document_id] >= per_document:
            continue
        counts[chunk.document_id] += 1
        result.append(Hit(chunk, score, len(result) + 1, method))
        if len(result) == limit:
            break
    return result


def _cap_hits(hits: list[Hit], limit: int, per_document: int) -> list[Hit]:
    counts: Counter[str] = Counter()
    result = []
    for hit in hits:
        if counts[hit.chunk.document_id] >= per_document:
            continue
        counts[hit.chunk.document_id] += 1
        result.append(Hit(hit.chunk, hit.score, len(result) + 1, hit.method))
        if len(result) == limit:
            break
    return result
