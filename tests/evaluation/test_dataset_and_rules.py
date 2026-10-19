import json
import shutil
import subprocess
import sys
from copy import deepcopy
from inspect import signature
from pathlib import Path
from uuid import uuid4

import pytest
from incidentiq.agent.output import Hypothesis, InvestigationReport
from incidentiq.agent.validators import validate_evidence_references
from incidentiq.baselines.cli import main as diagnose
from incidentiq.baselines.rules import run_rules
from incidentiq.evaluation.dataset import load_dataset
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals"
DEV = load_dataset(DATASET, split="dev")


def development_case(cause="slow_dependency", variant="clear"):
    return next(
        case
        for case in DEV
        if case.label.primary_root_cause == cause and case.label.configuration["variant"] == variant
    )


def test_frozen_dataset_counts_scopes_and_partition():
    cases = load_dataset(DATASET)
    assert len(cases) == 24
    assert len(DEV) == 16
    held_out = load_dataset(DATASET, split="held_out")
    assert len(held_out) == 8
    assert not {case.label.case_id for case in DEV} & {case.label.case_id for case in held_out}
    assert sum(case.label.expected_abstention for case in DEV) == 4
    assert {case.label.provenance for case in cases} == {"authored_synthetic_fixture"}
    assert all(case.label.configuration["live_telemetry_capture"] is False for case in cases)
    forbidden = {
        "case_id",
        "split",
        "scenario_id",
        "scenario_name",
        "primary_root_cause",
        "expected_abstention",
        "configuration",
    }
    assert all(not forbidden.intersection(case.bundle.model_dump()) for case in cases)


@pytest.mark.parametrize("case", DEV, ids=lambda case: case.label.case_id)
def test_rules_development_contracts_only(case):
    # The strategy receives neither label nor expectation. Do not run held-out cases here.
    report = run_rules(case.bundle)
    assert validate_evidence_references(report, case.bundle) == report
    if case.label.expected_abstention:
        assert report.disposition == "insufficient_evidence"
        assert report.hypotheses[0].cause == "unknown"
        assert report.hypotheses[0].confidence == 0
    else:
        assert report.disposition == "likely_cause_found"
        assert report.hypotheses[0].cause == case.label.primary_root_cause
        citations = set(report.hypotheses[0].supporting_evidence_ids)
        assert citations
        assert citations <= set(case.expected.relevant_evidence_ids)
        assert not citations & set(case.expected.irrelevant_evidence_ids)
    reversed_bundle = case.bundle.model_copy(
        update={"records": list(reversed(case.bundle.records))}
    )
    assert run_rules(reversed_bundle).model_dump_json() == report.model_dump_json()


def test_strategy_signature_has_no_ground_truth_or_scenario_arguments():
    assert list(signature(run_rules).parameters) == ["bundle"]


@pytest.mark.parametrize(
    "relative", ["cases.jsonl", "expected_evidence.jsonl", "fixtures/iq-001.json"]
)
def test_dataset_refuses_modified_frozen_inputs(tmp_path, relative):
    root = tmp_path / "evals"
    shutil.copytree(DATASET, root)
    with (root / relative).open("a", encoding="utf-8") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        load_dataset(root)


def test_dataset_refuses_manifest_path_escape(tmp_path):
    root = tmp_path / "evals"
    root.mkdir()
    manifest = json.loads((DATASET / "manifest.json").read_text())
    manifest["files"] = {"../outside.json": "0" * 64}
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        load_dataset(root)


def test_fixture_recipe_reproduces_frozen_bytes_and_refuses_overwrite(tmp_path):
    target = tmp_path / "fresh"
    command = [
        sys.executable,
        str(ROOT / "scripts/author_eval_fixtures.py"),
        "--output-root",
        str(target),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for relative in [
        "manifest.json",
        *json.loads((DATASET / "manifest.json").read_text())["files"],
    ]:
        assert (target / relative).read_bytes() == (DATASET / relative).read_bytes()
    before = (target / "manifest.json").read_bytes()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "Refusing to overwrite" in result.stderr
    assert (target / "manifest.json").read_bytes() == before


def test_report_rejects_fabricated_references():
    bundle = development_case().bundle
    report = run_rules(bundle)
    changed = report.model_copy(deep=True)
    changed.hypotheses[0].supporting_evidence_ids.append(uuid4())
    with pytest.raises(ValueError, match="nonexistent"):
        validate_evidence_references(changed, bundle)


def test_material_cause_requires_support_and_confidence_is_finite():
    bundle = development_case().bundle
    report = run_rules(bundle)
    report.hypotheses[0].supporting_evidence_ids = []
    with pytest.raises(ValueError, match="requires supporting"):
        validate_evidence_references(report, bundle)
    with pytest.raises(ValidationError):
        Hypothesis(
            cause="unknown",
            confidence=float("nan"),
            supporting_evidence_ids=[],
            contradicting_evidence_ids=[],
            explanation="Unavailable",
        )


def test_schema_rejects_duplicate_and_conflicting_citations():
    report = run_rules(development_case().bundle)
    hypothesis = report.hypotheses[0].model_dump()
    citation = hypothesis["supporting_evidence_ids"][0]
    with pytest.raises(ValidationError):
        Hypothesis.model_validate({**hypothesis, "contradicting_evidence_ids": [citation]})
    with pytest.raises(ValidationError):
        Hypothesis.model_validate({**hypothesis, "supporting_evidence_ids": [citation, citation]})


def test_empty_evidence_abstains():
    bundle = development_case().bundle.model_copy(update={"records": [], "status": "unavailable"})
    report = run_rules(bundle)
    assert report.disposition == "insufficient_evidence"
    assert report.missing_evidence
    assert not report.hypotheses[0].supporting_evidence_ids


def test_cumulative_metric_snapshot_does_not_establish_pool_exhaustion():
    bundle = deepcopy(development_case("database_pool_exhaustion").bundle)
    for record in bundle.records:
        if record.source_type == "metric":
            record.payload["kind"] = "sum"
            record.payload["aggregation_temporality"] = "cumulative"
    assert run_rules(bundle).disposition == "insufficient_evidence"


def test_unrelated_pool_names_do_not_combine_into_one_saturation_signal():
    bundle = deepcopy(development_case("database_pool_exhaustion").bundle)
    for record in bundle.records:
        if record.payload.get("name") == "incidentiq.db.pool.waiting":
            record.payload["attributes"]["incidentiq.pool.name"] = "different-pool"
    assert run_rules(bundle).disposition == "insufficient_evidence"


def test_fanout_without_explicit_retry_attributes_is_not_a_retry_storm():
    bundle = deepcopy(development_case("retry_storm").bundle)
    for record in bundle.records:
        record.payload.get("attributes", {}).pop("incidentiq.retry_attempt", None)
    assert run_rules(bundle).disposition == "insufficient_evidence"


def test_deployment_without_matching_revision_does_not_establish_a_cause():
    bundle = deepcopy(development_case("deployment_regression").bundle)
    for record in bundle.records:
        if record.source_type == "trace":
            record.payload.get("attributes", {}).pop("deployment.revision", None)
    assert run_rules(bundle).disposition == "insufficient_evidence"


def test_multiple_supported_causes_remain_inconclusive():
    slow = deepcopy(development_case().bundle)
    pool_records = [
        record
        for record in development_case("database_pool_exhaustion").bundle.records
        if record.source_type == "metric"
    ]
    # Move independently generated gauges into this incident's window for a mixed-signal test.
    shifted = [
        record.model_copy(
            update={
                "incident_id": slow.incident_id,
                "timestamp": slow.window.started_at
                + (
                    record.timestamp
                    - development_case("database_pool_exhaustion").bundle.window.started_at
                ),
            }
        )
        for record in pool_records
    ]
    slow.records.extend(shifted)
    report = run_rules(slow)
    assert report.disposition == "inconclusive"
    assert {hypothesis.cause for hypothesis in report.hypotheses} == {
        "slow_dependency",
        "database_pool_exhaustion",
    }
    assert report.missing_evidence


def test_scope_and_incident_isolation_are_enforced():
    bundle = deepcopy(development_case().bundle)
    bundle.records[0] = bundle.records[0].model_copy(update={"incident_id": uuid4()})
    with pytest.raises(ValueError, match="different incident"):
        run_rules(bundle)
    bundle = deepcopy(development_case().bundle)
    bundle.records[0] = bundle.records[0].model_copy(update={"service": "unselected-service"})
    with pytest.raises(ValueError, match="scope"):
        run_rules(bundle)


def test_diagnose_cli_uses_bundle_only_and_preserves_input(tmp_path, monkeypatch, capsys):
    source = tmp_path / "bundle.json"
    source.write_text(development_case().bundle.model_dump_json())
    before = source.read_bytes()
    monkeypatch.setattr(sys, "argv", ["diagnose", "--evidence", str(source)])
    assert diagnose() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["model"] is None
    assert output["strategy_version"] == "rules-v1"
    assert (
        InvestigationReport.model_validate(output["report"]).hypotheses[0].cause
        == "slow_dependency"
    )
    assert source.read_bytes() == before
    monkeypatch.setattr(
        sys, "argv", ["diagnose", "--evidence", str(source), "--output", str(source)]
    )
    with pytest.raises(SystemExit) as error:
        diagnose()
    assert error.value.code == 1
    assert source.read_bytes() == before
