from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from incidentiq.retrieval.knowledge import Hit, Retriever

if TYPE_CHECKING:
    from incidentiq.evaluation.dataset import DatasetCase

METHODS = ("lexical", "vector", "hybrid")


def case_query(case: DatasetCase) -> str:
    # Query is constructed only from the incident service and observed evidence.
    parts = [case.bundle.window.service]
    parts.extend(record.summary for record in case.bundle.records[:30])
    return " ".join(parts)[:4000]


def _score(hits: list[Hit], relevant: set) -> dict:
    ranks = [rank for rank, hit in enumerate(hits, 1) if hit.chunk.id in relevant]
    return {
        "recall_at_1": len(set(ranks) & {1}) / len(relevant) if relevant else 0.0,
        "recall_at_3": len(set(ranks) & {1, 2, 3}) / len(relevant) if relevant else 0.0,
        "recall_at_5": len(set(ranks) & {1, 2, 3, 4, 5}) / len(relevant)
        if relevant
        else 0.0,
        "reciprocal_rank": 1 / ranks[0] if ranks else 0.0,
        "relevant_count": len(relevant),
        "retrieved_relevant_count": len(ranks),
    }


def run_retrieval_evaluation(
    cases: list[DatasetCase], retriever: Retriever
) -> tuple[dict, list[dict]]:
    results = []
    metric_names = ("recall_at_1", "recall_at_3", "recall_at_5", "reciprocal_rank")
    aggregates = {method: {key: [] for key in metric_names} for method in METHODS}
    for case in cases:
        query = case_query(case)
        retrieved = {}
        for method in METHODS:
            retrieved[method] = getattr(retriever, method)(query, 5)
        relevant = {
            chunk.id for chunk in retriever.chunks if case.label.primary_root_cause in chunk.tags
        }
        strategies = {}
        for method, hits in retrieved.items():
            metrics = _score(hits, relevant)
            for key in aggregates[method]:
                aggregates[method][key].append(metrics[key])
            strategies[method] = {
                **metrics,
                "results": [
                    {
                        "id": str(hit.chunk.id),
                        "document_id": hit.chunk.document_id,
                        "section": hit.chunk.section,
                        "source": hit.chunk.source,
                        "document_type": hit.chunk.document_type,
                        "service": hit.chunk.service,
                        "score": hit.score,
                        "snippet": hit.chunk.text[:500],
                    }
                    for hit in hits
                ],
            }
        results.append({"case_id": case.label.case_id, "query": query, "methods": strategies})
    summary = {
        method: {
            key: sum(values) / len(values) if values else 0.0
            for key, values in metrics.items()
        }
        for method, metrics in aggregates.items()
    }
    return {"metrics": summary}, results


def write_retrieval_report(directory: Path, metadata: dict, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {**metadata, "results": rows}
    (directory / "retrieval.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (directory / "retrieval-results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    lines = [
        "# IncidentIQ retrieval evaluation", "",
        f"- Corpus: `{metadata['corpus_version']}` ({metadata['chunk_count']} chunks)",
        f"- Dataset: `{metadata['dataset_version']}`; split: `{metadata['split']}`",
        f"- Embeddings: `{metadata['embedding_model']}`", "",
        "| Method | Recall@1 | Recall@3 | Recall@5 | MRR |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for method, metrics in metadata["metrics"].items():
        lines.append(
            f"| {method} | {metrics['recall_at_1']:.4f} | {metrics['recall_at_3']:.4f} "
            f"| {metrics['recall_at_5']:.4f} | {metrics['reciprocal_rank']:.4f} |"
        )
    lines += [
        "",
        "Relevance is based on authored synthetic query judgments.",
        "",
    ]
    (directory / "retrieval.md").write_text("\n".join(lines), encoding="utf-8")
