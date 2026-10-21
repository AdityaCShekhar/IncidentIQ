"""Scoring and accounting checks; fake model results are never quality evidence."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from incidentiq.baselines.rules import run_rules
from incidentiq.evaluation.benchmark import evaluate, write_comparison, write_reports
from incidentiq.evaluation.cli import main
from incidentiq.evaluation.dataset import load_dataset

DEV = load_dataset(Path(__file__).resolve().parents[2] / "evals", split="dev")


def test_rules_scoring_and_resource_scope(tmp_path):
    metadata, rows = evaluate(DEV)
    metrics = metadata["metrics"]
    assert metrics["correct_cases"] == 16
    assert metrics["failed_cases"] == 0
    assert metrics["abstention_recall"] == 1
    assert metrics["p50_case_latency_ms"] <= metrics["p95_case_latency_ms"]
    assert metrics["p95_case_latency_ms"] <= metrics["max_case_latency_ms"]
    assert metrics["process_cpu_time_ms"] >= 0
    assert all(row["process_cpu_time_ms"] >= 0 for row in rows)
    assert "excludes model server" in metadata["resource_measurement"]["scope"]
    write_reports(tmp_path, metadata, rows)
    saved = json.loads((tmp_path / "benchmark.json").read_text())
    assert saved["metrics"] == metrics
    assert len((tmp_path / "raw-results.jsonl").read_text().splitlines()) == 16
    assert "model-server resources are excluded" in (tmp_path / "benchmark.md").read_text()


def test_failures_count_as_incorrect_and_unknown_token_usage_is_not_zero(tmp_path):
    calls = 0

    def strategy(bundle):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("model_timeout")
        return SimpleNamespace(
            report=run_rules(bundle),
            raw_output="fixture response",
            metadata={"prompt_tokens": 10, "completion_tokens": 5},
        )

    metadata, rows = evaluate(DEV[:2], strategy=strategy, strategy_name="test-double")
    metrics = metadata["metrics"]
    assert metrics["accuracy"] == 0.5
    assert metrics["failure_rate"] == 0.5
    assert metrics["failure_counts"] == {"model_timeout": 1}
    assert metrics["prompt_tokens"] is None
    assert metrics["completion_tokens"] is None
    assert rows[1]["report"] is None
    assert rows[1]["status"] == "failed"
    assert rows[1]["error_code"] == "model_timeout"
    write_comparison(tmp_path, {"test-double": metadata})
    saved = json.loads((tmp_path / "comparison.json").read_text())
    assert saved["strategies"]["test-double"]["failure_counts"] == {"model_timeout": 1}


def test_complete_token_accounting_and_empty_evaluation():
    def strategy(bundle):
        return SimpleNamespace(
            report=run_rules(bundle), metadata={"prompt_tokens": 10, "completion_tokens": 5}
        )

    metadata, _ = evaluate(DEV[:2], strategy=strategy)
    assert metadata["metrics"]["prompt_tokens"] == 20
    assert metadata["metrics"]["completion_tokens"] == 10
    empty, _ = evaluate([])
    assert empty["metrics"]["p95_case_latency_ms"] == 0
    assert empty["metrics"]["process_peak_rss_bytes"] is None
    assert empty["metrics"]["prompt_tokens"] is None


def test_held_out_still_requires_explicit_final_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        [
            "benchmark",
            "--split",
            "held_out",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not (tmp_path / "reports").exists()
