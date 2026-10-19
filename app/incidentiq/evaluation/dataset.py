import argparse
import hashlib
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from incidentiq.ingestion.normalize import digest, nanoseconds, timestamp
from incidentiq.ingestion.schemas import EvidenceBundle, EvidenceWindow, ServiceName

Cause = Literal[
    "slow_dependency", "database_pool_exhaustion", "deployment_regression", "retry_storm"
]
Split = Literal["dev", "held_out"]


class LabelledCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^iq-\d{3}$")
    scenario_name: Cause
    provenance: Literal["authored_synthetic_fixture"]
    split: Split
    seed: int = Field(ge=0)
    configuration: dict
    window: EvidenceWindow
    primary_root_cause: Cause
    acceptable_aliases: list[str] = Field(min_length=1)
    affected_services: list[ServiceName] = Field(min_length=1)
    dependency_chain: list[ServiceName] = Field(min_length=1)
    plausible_alternative_causes: list[Cause] = Field(min_length=1)
    recommended_safe_next_step: str = Field(min_length=1)
    severity: Literal["critical", "high", "medium", "low"]
    expected_abstention: bool
    missing_evidence: list[str]
    evidence_file: str = Field(pattern=r"^fixtures/iq-\d{3}\.json$")

    @model_validator(mode="after")
    def coherent_label(self):
        if self.scenario_name != self.primary_root_cause:
            raise ValueError("Scenario and primary cause must agree")
        if self.window.service not in self.affected_services:
            raise ValueError("Target service must be affected")
        if self.primary_root_cause in self.plausible_alternative_causes:
            raise ValueError("A cause cannot be its own alternative")
        if self.expected_abstention != bool(self.missing_evidence):
            raise ValueError("Abstention cases must name the missing diagnostic evidence")
        if self.evidence_file != f"fixtures/{self.case_id}.json":
            raise ValueError("Case must use its own evidence fixture")
        return self


class ExpectedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    relevant_evidence_ids: list[UUID]
    irrelevant_evidence_ids: list[UUID]
    evidence_roles: dict[UUID, str]
    predicates: list[str] = Field(min_length=1)


class DatasetCase(BaseModel):
    label: LabelledCase
    expected: ExpectedEvidence
    bundle: EvidenceBundle


class FreezeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: Literal["fixture-v1"]
    provenance: Literal["authored_synthetic_fixture"]
    case_count: int = Field(ge=20, le=30)
    split_counts: dict[Split, int]
    files: dict[str, str]


def read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Dataset file exceeds its size limit")
    return data


def load_dataset(root: Path, *, split: Split | None = None) -> list[DatasetCase]:
    """Validate frozen inputs, then select a split. Never pass labels to a strategy."""
    root = root.resolve()
    manifest = FreezeManifest.model_validate_json(read_bounded(root / "manifest.json", 64 * 1024))
    contents = {}
    for relative, checksum in manifest.files.items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Dataset path escapes its root")
        data = read_bounded(path, 4 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError(
                "Frozen dataset checksum mismatch; create a new version to change cases"
            )
        contents[relative] = data
    labels = [
        LabelledCase.model_validate_json(line) for line in contents["cases.jsonl"].splitlines()
    ]
    expectations = [
        ExpectedEvidence.model_validate_json(line)
        for line in contents["expected_evidence.jsonl"].splitlines()
    ]
    expected = {item.case_id: item for item in expectations}
    if len(expected) != len(expectations) or len({item.case_id for item in labels}) != len(labels):
        raise ValueError("Duplicate case IDs")
    if len(labels) != manifest.case_count or set(expected) != {item.case_id for item in labels}:
        raise ValueError("Dataset case and expectation sets must agree")
    files = {"cases.jsonl", "expected_evidence.jsonl", *(item.evidence_file for item in labels)}
    if set(contents) != files:
        raise ValueError(
            "Manifest must cover exactly the labels, expectations and evidence fixtures"
        )
    counts = {part: sum(item.split == part for item in labels) for part in ("dev", "held_out")}
    if counts != manifest.split_counts or any(count == 0 for count in counts.values()):
        raise ValueError("Frozen split counts do not agree")
    result = []
    for label in labels:
        bundle = EvidenceBundle.model_validate_json(contents[label.evidence_file])
        gold = expected[label.case_id]
        if bundle.window != label.window:
            raise ValueError("Evidence and labelled incident windows differ")
        if len(bundle.records) > bundle.limits.max_records:
            raise ValueError("Evidence bundle exceeds its record budget")
        ids = {record.id for record in bundle.records}
        if len(ids) != len(bundle.records):
            raise ValueError("Duplicate evidence IDs")
        for record in bundle.records:
            at = int(record.payload["timestamp_unix_nano"])
            if (
                record.incident_id != bundle.incident_id
                or record.service not in bundle.window.services
                or not nanoseconds(bundle.window.started_at)
                <= at
                < nanoseconds(bundle.window.ended_at)
                or record.timestamp != timestamp(at)
            ):
                raise ValueError("Evidence falls outside its incident scope")
            canonical = record.model_dump(
                mode="json",
                include={"source_type", "service", "source_uri", "severity", "summary", "payload"},
            )
            content_hash = digest(canonical)
            record_id = uuid5(
                NAMESPACE_URL, f"incidentiq:evidence:v1:{bundle.incident_id}:{content_hash}"
            )
            if record.content_hash != content_hash or record.id != record_id:
                raise ValueError("Evidence identity does not match normalized content")
        relevant, irrelevant = set(gold.relevant_evidence_ids), set(gold.irrelevant_evidence_ids)
        if (
            len(relevant) != len(gold.relevant_evidence_ids)
            or len(irrelevant) != len(gold.irrelevant_evidence_ids)
            or relevant & irrelevant
            or relevant | irrelevant != ids
            or set(gold.evidence_roles) != ids
        ):
            raise ValueError("Expected evidence must classify every observation exactly once")
        if not label.expected_abstention and not relevant:
            raise ValueError("A sufficient case requires relevant evidence")
        if split is None or label.split == split:
            result.append(DatasetCase(label=label, expected=gold, bundle=bundle))
    return sorted(result, key=lambda item: item.label.case_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frozen synthetic evaluation inputs")
    parser.add_argument("--directory", type=Path, default=Path("evals"))
    args = parser.parse_args()
    try:
        cases = load_dataset(args.directory)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(
            1, "Dataset validation failed: check schemas, labels, paths and frozen checksums.\n"
        )
    print(f"Validated {len(cases)} authored synthetic cases; no diagnosis strategies executed.")
    for split in ("dev", "held_out"):
        selected = [item for item in cases if item.label.split == split]
        print(
            f"{split}: {len(selected)} cases; "
            f"{sum(item.label.expected_abstention for item in selected)} expected abstentions"
        )
    return 0
