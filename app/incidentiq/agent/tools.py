import re
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError

from incidentiq.ingestion.schemas import EvidenceBundle, EvidenceRecord, SourceType
from incidentiq.retrieval.knowledge import Retriever

TOOL_LIMIT = 5
TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


class EvidenceSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    service: str | None = Field(default=None, max_length=100)
    source: SourceType | None = None


class EvidenceLookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID


class KnowledgeSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=300)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["search_evidence", "get_evidence", "retrieve_knowledge"]
    arguments: dict = Field(max_length=4)


class ToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ToolRequest] = Field(max_length=TOOL_LIMIT)


class ToolAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    input: dict
    output_summary: dict
    status: Literal["succeeded", "failed", "timed_out"]
    latency_ms: float
    error_code: str | None = None


def _record_summary(record: EvidenceRecord) -> dict:
    return {
        "evidence_id": str(record.id),
        "source_type": record.source_type,
        "service": record.service,
        "timestamp": record.timestamp.isoformat(),
        "severity": record.severity,
        "summary": record.summary[:500],
    }


class ToolRegistry:
    """Only exposes bounded read-only operations over the supplied bundle and corpus."""

    def __init__(self, bundle: EvidenceBundle, retriever: Retriever):
        self.bundle = bundle
        self.retriever = retriever
        self.by_id = {record.id: record for record in bundle.records}
        self._schemas = {
            "search_evidence": EvidenceSearchArgs,
            "get_evidence": EvidenceLookupArgs,
            "retrieve_knowledge": KnowledgeSearchArgs,
        }

    @property
    def descriptions(self) -> list[dict]:
        return [
            {
                "name": "search_evidence",
                "description": "Search the supplied incident evidence by terms and optional service/source filters.",
                "arguments_schema": EvidenceSearchArgs.model_json_schema(),
            },
            {
                "name": "get_evidence",
                "description": "Fetch one supplied evidence record by its UUID.",
                "arguments_schema": EvidenceLookupArgs.model_json_schema(),
            },
            {
                "name": "retrieve_knowledge",
                "description": "Search curated runbooks and prior incident sections.",
                "arguments_schema": KnowledgeSearchArgs.model_json_schema(),
            },
        ]

    def execute(self, name: str, arguments: dict, *, timeout_seconds: float) -> ToolAudit:
        if name not in self._schemas:
            return ToolAudit(
                tool_name="unknown",
                input={},
                output_summary={},
                status="failed",
                latency_ms=0,
                error_code="tool_not_allowed",
            )
        started = time.perf_counter()
        try:
            if timeout_seconds <= 0:
                raise ValueError("invalid_timeout")
            args = self._schemas[name].model_validate(arguments)
            if name == "search_evidence":
                result = self._search_evidence(args)
            elif name == "get_evidence":
                result = self._get_evidence(args)
            else:
                result = self._retrieve_knowledge(args)
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed > timeout_seconds * 1000:
                return ToolAudit(
                    tool_name=name,
                    input=args.model_dump(mode="json"),
                    output_summary={},
                    status="timed_out",
                    latency_ms=elapsed,
                    error_code="tool_timeout",
                )
            return ToolAudit(
                tool_name=name,
                input=args.model_dump(mode="json"),
                output_summary=result,
                status="succeeded",
                latency_ms=elapsed,
            )
        except Exception as error:
            elapsed = (time.perf_counter() - started) * 1000
            code = "invalid_arguments" if isinstance(error, ValidationError) else "tool_failed"
            return ToolAudit(
                tool_name=name,
                input={
                    "provided_keys": sorted(arguments)[:8]
                    if isinstance(arguments, dict)
                    else []
                },
                output_summary={},
                status="failed",
                latency_ms=elapsed,
                error_code=code,
            )

    def _search_evidence(self, args: EvidenceSearchArgs) -> dict:
        terms = set(token.lower() for token in TOKEN.findall(args.query))
        ranked = []
        for record in self.bundle.records:
            if args.service and record.service != args.service:
                continue
            if args.source and record.source_type != args.source:
                continue
            words = set(token.lower() for token in TOKEN.findall(record.summary))
            score = len(terms & words)
            if score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], str(item[1].id)))
        selected = ranked[:20]
        return {
            "count": len(selected),
            "evidence_ids": [str(record.id) for _, record in selected],
            "items": [_record_summary(record) for _, record in selected],
        }

    def _get_evidence(self, args: EvidenceLookupArgs) -> dict:
        record = self.by_id.get(args.evidence_id)
        if record is None:
            return {"count": 0, "evidence_ids": [], "items": []}
        return {"count": 1, "evidence_ids": [str(record.id)], "items": [_record_summary(record)]}

    def _retrieve_knowledge(self, args: KnowledgeSearchArgs) -> dict:
        hits = self.retriever.hybrid(args.query, limit=5, per_document=2)
        return {
            "count": len(hits),
            "chunk_ids": [str(hit.chunk.id) for hit in hits],
            "items": [
                {
                    "chunk_id": str(hit.chunk.id),
                    "source": hit.chunk.source,
                    "section": hit.chunk.section,
                    "snippet": hit.chunk.text[:700],
                }
                for hit in hits
            ],
        }
