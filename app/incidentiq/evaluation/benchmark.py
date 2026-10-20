"""Deterministic benchmark runner and report rendering for diagnosis strategies."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

from incidentiq.agent.output import InvestigationReport
from incidentiq.agent.validators import validate_evidence_references
from incidentiq.baselines.rules import RULES_VERSION, run_rules
from incidentiq.evaluation.dataset import DatasetCase, load_dataset

STRATEGY = "rules"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _metrics(rows: list[dict]) -> dict:
    total = len(rows)
    abstained = sum(row["predicted_cause"] is None for row in rows)
    diagnosed = total - abstained
    correct = sum(row["correct"] for row in rows)
    labeled = [row for row in rows if row["predicted_cause"] is not None]
    citation_tp = sum(row["citation_true_positive"] for row in rows)
    citation_fp = sum(row["citation_false_positive"] for row in rows)
    citation_fn = sum(row["citation_false_negative"] for row in rows)
    return {
        "case_count": total,
        "correct_cases": correct,
        "accuracy": correct / total if total else 0.0,
        "coverage": diagnosed / total if total else 0.0,
        "abstention_rate": abstained / total if total else 0.0,
        "selective_accuracy": sum(row["correct"] for row in labeled) / diagnosed
        if diagnosed
        else 0.0,
        "macro_f1": _macro_f1(rows),
        "abstention_recall": sum(
            row["expected_abstention"] and row["predicted_cause"] is None for row in rows
        ) / sum(row["expected_abstention"] for row in rows)
        if any(row["expected_abstention"] for row in rows)
        else None,
        "citation_precision": citation_tp / (citation_tp + citation_fp)
        if citation_tp + citation_fp
        else 0.0,
        "citation_recall": citation_tp / (citation_tp + citation_fn)
        if citation_tp + citation_fn
        else 0.0,
        "citation_f1": 2 * citation_tp / (2 * citation_tp + citation_fp + citation_fn)
        if 2 * citation_tp + citation_fp + citation_fn
        else 0.0,
        "citation_counts": {
            "true_positive": citation_tp,
            "false_positive": citation_fp,
            "false_negative": citation_fn,
        },
        "confusion_matrix": _confusion(rows),
    }


def _macro_f1(rows: list[dict]) -> float:
    labels = sorted({row["gold_cause"] for row in rows})
    scores = []
    for label in labels:
        tp = sum(row["predicted_cause"] == label and row["gold_cause"] == label for row in rows)
        fp = sum(row["predicted_cause"] == label and row["gold_cause"] != label for row in rows)
        fn = sum(row["predicted_cause"] != label and row["gold_cause"] == label for row in rows)
        scores.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _confusion(rows: list[dict]) -> dict:
    matrix: dict[str, dict[str, int]] = {}
    for row in rows:
        actual = row["gold_cause"]
        predicted = row["predicted_cause"] or "abstain"
        matrix.setdefault(actual, {})
        matrix[actual][predicted] = matrix[actual].get(predicted, 0) + 1
    return matrix


def evaluate(
    cases: list[DatasetCase],
    strategy: Callable = run_rules,
    *,
    strategy_name: str = STRATEGY,
    strategy_version: str = RULES_VERSION,
    dataset_version: str = "fixture-v1",
    split: str = "dev",
) -> tuple[dict, list[dict]]:
    """Evaluate one strategy; labels are read only after the strategy returns."""
    rows = []
    for case in cases:
        report: InvestigationReport = strategy(case.bundle)
        validate_evidence_references(report, case.bundle)
        top = report.hypotheses[0]
        predicted = None if report.disposition == "insufficient_evidence" else top.cause
        accepted = {case.label.primary_root_cause, *case.label.acceptable_aliases}
        citations = set(top.supporting_evidence_ids)
        relevant = set(case.expected.relevant_evidence_ids)
        tp, fp, fn = len(citations & relevant), len(citations - relevant), len(relevant - citations)
        rows.append(
            {
                "case_id": case.label.case_id,
                "gold_cause": case.label.primary_root_cause,
                "acceptable_aliases": case.label.acceptable_aliases,
                "expected_abstention": case.label.expected_abstention,
                "predicted_cause": predicted,
                "disposition": report.disposition,
                "correct": (predicted is None and case.label.expected_abstention)
                or (predicted in accepted and not case.label.expected_abstention),
                "citation_true_positive": tp,
                "citation_false_positive": fp,
                "citation_false_negative": fn,
                "report": report.model_dump(mode="json"),
            }
        )
    metadata = {
        "dataset_version": dataset_version,
        "split": split,
        "strategy": strategy_name,
        "strategy_version": strategy_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": _metrics(rows),
    }
    return metadata, rows


def markdown_report(metadata: dict, rows: list[dict]) -> str:
    metrics = metadata["metrics"]
    lines = [
        "# IncidentIQ benchmark report",
        "",
        f"- Dataset: `{metadata['dataset_version']}`",
        f"- Split: `{metadata['split']}`",
        f"- Strategy: `{metadata['strategy']}` (`{metadata['strategy_version']}`)",
        f"- Generated: `{metadata['generated_at']}`",
        f"- Cases: {metrics['case_count']}", "",
        "## Metrics", "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in (
        "accuracy",
        "macro_f1",
        "coverage",
        "abstention_rate",
        "selective_accuracy",
        "abstention_recall",
        "citation_precision",
        "citation_recall",
        "citation_f1",
    ):
        value = metrics[key]
        formatted = "n/a" if value is None else f"{value:.4f}"
        lines.append(f"| {key.replace('_', ' ').title()} | {formatted} |")
    lines += [
        "",
        "## Per-case results",
        "",
        "| Case | Gold | Prediction | Abstain expected | Correct |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['gold_cause']} | {row['predicted_cause'] or 'abstain'} "
            f"| {'yes' if row['expected_abstention'] else 'no'} | {'yes' if row['correct'] else 'no'} |"
        )
    lines += [
        "",
        "Authored synthetic fixtures; values are not measured production telemetry.",
        "",
    ]
    return "\n".join(lines)


def write_reports(output_dir: Path, metadata: dict, rows: list[dict]) -> None:
    raw = {**metadata, "results": rows}
    _atomic_write(output_dir / "benchmark.json", json.dumps(raw, indent=2, sort_keys=True) + "\n")
    _atomic_write(output_dir / "benchmark.md", markdown_report(metadata, rows))
    _atomic_write(
        output_dir / "raw-results.jsonl",
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
    )
