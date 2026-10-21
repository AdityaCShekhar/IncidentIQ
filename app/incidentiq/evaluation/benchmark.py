"""Deterministic benchmark runner and report rendering for diagnosis strategies."""

import json
import math
import os
import platform
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from time import perf_counter, process_time

from incidentiq.agent.output import InvestigationReport
from incidentiq.agent.validators import validate_evidence_references
from incidentiq.baselines.rules import RULES_VERSION, run_rules
from incidentiq.evaluation.dataset import DatasetCase

STRATEGY = "rules"


def _process_peak_rss_bytes() -> int | None:
    """Process lifetime high-water mark; excludes a separate model server."""
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if platform.system() == "Darwin" else value * 1024)
    except (ImportError, OSError):
        return None


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
    failed = sum(row["status"] == "failed" for row in rows)
    abstained = sum(row["status"] == "abstained" for row in rows)
    diagnosed = total - abstained - failed
    correct = sum(row["correct"] for row in rows)
    labeled = [row for row in rows if row["predicted_cause"] is not None]
    citation_tp = sum(row["citation_true_positive"] for row in rows)
    citation_fp = sum(row["citation_false_positive"] for row in rows)
    citation_fn = sum(row["citation_false_negative"] for row in rows)
    latencies = [row["wall_time_ms"] for row in rows]
    cpu_times = [row["process_cpu_time_ms"] for row in rows]
    peak_rss = [row["process_peak_rss_bytes"] for row in rows]
    token_rows = [row["strategy_metadata"] or {} for row in rows]

    def token_total(name: str):
        values = [row.get(name) for row in token_rows]
        return sum(values) if values and all(isinstance(value, int) for value in values) else None

    return {
        "case_count": total,
        "failed_cases": failed,
        "failure_rate": failed / total if total else 0.0,
        "mean_case_latency_ms": round(mean(latencies), 3) if latencies else 0.0,
        "p50_case_latency_ms": round(median(latencies), 3) if latencies else 0.0,
        "max_case_latency_ms": round(max(latencies), 3) if latencies else 0.0,
        "p95_case_latency_ms": sorted(latencies)[math.ceil(0.95 * total) - 1] if total else 0.0,
        "process_cpu_time_ms": round(sum(cpu_times), 3),
        "process_peak_rss_bytes": max(
            (value for value in peak_rss if value is not None), default=None
        ),
        "failure_counts": dict(
            sorted(Counter(row["error_code"] for row in rows if row["status"] == "failed").items())
        ),
        "prompt_tokens": token_total("prompt_tokens"),
        "completion_tokens": token_total("completion_tokens"),
        "correct_cases": correct,
        "accuracy": correct / total if total else 0.0,
        "coverage": diagnosed / total if total else 0.0,
        "abstention_rate": abstained / total if total else 0.0,
        "selective_accuracy": sum(row["correct"] for row in labeled) / diagnosed
        if diagnosed
        else 0.0,
        "macro_f1": _macro_f1(rows),
        "abstention_recall": sum(
            row["expected_abstention"] and row["status"] == "abstained" for row in rows
        )
        / sum(row["expected_abstention"] for row in rows)
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
        predicted = "failed" if row["status"] == "failed" else row["predicted_cause"] or "abstain"
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
        started = perf_counter()
        cpu_started = process_time()
        try:
            result = strategy(case.bundle)
            report: InvestigationReport = getattr(result, "report", result)
            validate_evidence_references(report, case.bundle)
            top = report.hypotheses[0]
            predicted = None if report.disposition == "insufficient_evidence" else top.cause
            accepted = {case.label.primary_root_cause, *case.label.acceptable_aliases}
            citations = set(top.supporting_evidence_ids)
            relevant = set(case.expected.relevant_evidence_ids)
            tp, fp, fn = (
                len(citations & relevant),
                len(citations - relevant),
                len(relevant - citations),
            )
            status = "abstained" if predicted is None else "completed"
            correct = (predicted is None and case.label.expected_abstention) or (
                predicted in accepted and not case.label.expected_abstention
            )
            report_value = report.model_dump(mode="json")
            strategy_metadata = getattr(result, "metadata", None)
            raw_output = getattr(result, "raw_output", None)
            error_code = None
        except Exception as error:
            predicted = None
            tp, fp, fn = 0, 0, len(case.expected.relevant_evidence_ids)
            status = "failed"
            correct = False
            report_value = None
            strategy_metadata = getattr(error, "metadata", None)
            raw_output = getattr(error, "raw_output", None)
            error_code = (
                str(error)
                if str(error)
                in {
                    "model_timeout",
                    "model_unavailable",
                    "model_response_too_large",
                    "invalid_model_response",
                    "invalid_model_output",
                    "agent_planning_failed",
                    "agent_diagnosis_failed",
                }
                else type(error).__name__
            )
        rows.append(
            {
                "case_id": case.label.case_id,
                "gold_cause": case.label.primary_root_cause,
                "acceptable_aliases": case.label.acceptable_aliases,
                "expected_abstention": case.label.expected_abstention,
                "predicted_cause": predicted,
                "status": status,
                "disposition": report.disposition if status != "failed" else "failed",
                "correct": correct,
                "citation_true_positive": tp,
                "citation_false_positive": fp,
                "citation_false_negative": fn,
                "report": report_value,
                "strategy_metadata": strategy_metadata,
                "raw_output": raw_output,
                "error_code": error_code,
                "wall_time_ms": round((perf_counter() - started) * 1000, 3),
                "process_cpu_time_ms": round((process_time() - cpu_started) * 1000, 3),
                "process_peak_rss_bytes": _process_peak_rss_bytes(),
            }
        )
    metadata = {
        "dataset_version": dataset_version,
        "split": split,
        "strategy": strategy_name,
        "strategy_version": strategy_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "resource_measurement": {
            "scope": "benchmark_process_only; excludes model server and child processes",
            "cpu": "process CPU time consumed during case evaluation",
            "memory": (
                "process lifetime peak RSS; not a per-case allocation or isolated strategy peak"
            ),
            "platform": platform.system(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
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
        f"- Cases: {metrics['case_count']}",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in (
        "failed_cases",
        "failure_rate",
        "accuracy",
        "macro_f1",
        "coverage",
        "abstention_rate",
        "selective_accuracy",
        "abstention_recall",
        "citation_precision",
        "citation_recall",
        "citation_f1",
        "mean_case_latency_ms",
        "p50_case_latency_ms",
        "max_case_latency_ms",
        "p95_case_latency_ms",
        "process_cpu_time_ms",
        "process_peak_rss_bytes",
        "prompt_tokens",
        "completion_tokens",
    ):
        value = metrics[key]
        formatted = "n/a" if value is None else f"{value:.4f}"
        lines.append(f"| {key.replace('_', ' ').title()} | {formatted} |")
    lines += [
        "",
        "## Per-case results",
        "",
        "| Case | Gold | Prediction | Status | Abstain expected | Correct | Error |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['gold_cause']} | {row['predicted_cause'] or 'abstain'} "
            f"| {row['status']} "
            f"| {'yes' if row['expected_abstention'] else 'no'} "
            f"| {'yes' if row['correct'] else 'no'} | {row['error_code'] or ''} |"
        )
    lines += [
        "",
        "Authored synthetic fixtures; values are not measured production telemetry.",
        "CPU and lifetime peak RSS cover the benchmark process only; model-server resources "
        "are excluded. RSS is not an isolated per-case or per-strategy peak.",
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


def write_comparison(output_dir: Path, reports: dict[str, dict]) -> None:
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": next(iter(reports.values()))["dataset_version"],
        "split": next(iter(reports.values()))["split"],
        "strategies": {
            name: {"strategy_version": report["strategy_version"], **report["metrics"]}
            for name, report in reports.items()
        },
        "resource_measurements": {
            name: report.get("resource_measurement") for name, report in reports.items()
        },
    }
    lines = [
        "# IncidentIQ strategy comparison",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "| Strategy | Version | Accuracy | Macro F1 | Coverage | Citation F1 | Failures "
        "| Mean latency (ms) | Prompt tokens | Completion tokens |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, report in reports.items():
        values = report["metrics"]
        completion_tokens = values["completion_tokens"]
        lines.append(
            f"| {name} | {report['strategy_version']} | {values['accuracy']:.4f} "
            f"| {values['macro_f1']:.4f} | {values['coverage']:.4f} "
            f"| {values['citation_f1']:.4f} | {values['failed_cases']} "
            f"| {values['mean_case_latency_ms']:.3f} "
            f"| {values['prompt_tokens'] if values['prompt_tokens'] is not None else 'n/a'} "
            f"| {completion_tokens if completion_tokens is not None else 'n/a'} |"
        )
    lines.extend(
        [
            "",
            "## Latency and benchmark-process resources",
            "",
            "| Strategy | p50 (ms) | p95 (ms) | Process CPU (ms) | Lifetime peak RSS (bytes) |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, report in reports.items():
        values = report["metrics"]
        lines.append(
            f"| {name} | {values['p50_case_latency_ms']:.3f} "
            f"| {values.get('p95_case_latency_ms', 'n/a')} "
            f"| {values.get('process_cpu_time_ms', 'n/a')} "
            f"| {values.get('process_peak_rss_bytes') or 'n/a'} |"
        )
    lines.extend(
        [
            "",
            "CPU and lifetime peak RSS exclude the model server. RSS accumulates across "
            "strategies in the same process; it is not an isolated strategy memory comparison.",
        ]
    )
    lines.extend(
        ["", "Per-strategy JSON, Markdown, and raw case artifacts are in sibling directories.", ""]
    )
    _atomic_write(
        output_dir / "comparison.json", json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write(output_dir / "comparison.md", "\n".join(lines))
