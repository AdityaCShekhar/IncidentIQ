import argparse
import json
import os
import tempfile
from pathlib import Path

from incidentiq.baselines.rules import RULES_VERSION, run_rules
from incidentiq.evaluation.dataset import read_bounded
from incidentiq.ingestion.schemas import EvidenceBundle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run S0 rules on one evidence bundle; no labels or model required"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        if args.output and args.output.resolve() == args.evidence.resolve():
            raise ValueError("Output must not overwrite input evidence")
        bundle = EvidenceBundle.model_validate_json(read_bounded(args.evidence, 4 * 1024 * 1024))
        report = run_rules(bundle)
        output = (
            json.dumps(
                {
                    "strategy": "rules",
                    "strategy_version": RULES_VERSION,
                    "incident_id": str(bundle.incident_id),
                    "model": None,
                    "report": report.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n"
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=args.output.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(output)
            os.replace(temporary, args.output)
        else:
            print(output, end="")
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(
            1, "Rules diagnosis failed: invalid evidence, references, scope or output path.\n"
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 0
