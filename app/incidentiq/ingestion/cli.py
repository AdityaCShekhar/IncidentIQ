import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from incidentiq.ingestion.schemas import CollectionLimits, EvidenceWindow
from incidentiq.ingestion.telemetry import FILE_NAME, collect_evidence


def window_from_scenario(
    path: Path, phase: str, service: str, related_services: list[str]
) -> EvidenceWindow:
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("Scenario artifact exceeds 64 KiB")
    document = json.loads(raw)
    phases = [item for item in document["phases"] if item["name"] == phase]
    if len(phases) != 1:
        raise ValueError("Scenario phase must match exactly once")
    # Read time boundaries only. Fault configuration and labels never enter the evidence bundle.
    return EvidenceWindow(
        service=service,
        related_services=tuple(related_services),
        started_at=phases[0]["started_at"],
        ended_at=phases[0]["ended_at"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect bounded OTLP JSON files into normalized evidence"
    )
    parser.add_argument("--directory", type=Path, default=Path("/telemetry"))
    parser.add_argument("--scenario-file", type=Path)
    parser.add_argument("--phase", choices=["baseline", "fault", "recovery"], default="fault")
    parser.add_argument("--started-at")
    parser.add_argument("--ended-at")
    parser.add_argument("--service", default="checkout")
    parser.add_argument("--related-service", action="append", default=[])
    parser.add_argument("--incident-id", type=UUID)
    parser.add_argument("--max-records", type=int, default=1000)
    parser.add_argument("--output", type=Path, help="Atomic JSON file output; default is stdout")
    args = parser.parse_args(argv)
    try:
        if args.output:
            target = args.output.resolve()
            if (target.parent == args.directory.resolve() and FILE_NAME.fullmatch(target.name)) or (
                args.scenario_file and target == args.scenario_file.resolve()
            ):
                raise ValueError("Output must not overwrite input telemetry or scenario artifact")
        if args.scenario_file:
            if args.started_at or args.ended_at:
                raise ValueError("Choose scenario phase or explicit timestamps")
            window = window_from_scenario(
                args.scenario_file, args.phase, args.service, args.related_service
            )
        else:
            if not args.started_at or not args.ended_at:
                raise ValueError("Provide both timestamps or --scenario-file")
            window = EvidenceWindow(
                service=args.service,
                related_services=tuple(args.related_service),
                started_at=args.started_at,
                ended_at=args.ended_at,
            )
        limits = CollectionLimits(max_records=args.max_records)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        ValidationError,
    ):
        parser.exit(
            1,
            "Invalid evidence window, limits, or scenario artifact. "
            "Use aware timestamps and a window of at most one hour.\n",
        )
    bundle = collect_evidence(args.directory, window, incident_id=args.incident_id, limits=limits)
    output = bundle.model_dump_json(indent=2) + "\n"
    if args.output:
        temporary = None
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=args.output.parent, encoding="utf-8", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(output)
            os.replace(temporary, args.output)
        except OSError:
            parser.exit(1, "Unable to save evidence bundle.\n")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    else:
        sys.stdout.write(output)
    print(
        f"Evidence status: {bundle.status}; {len(bundle.records)} records; "
        f"{len(bundle.issues)} issue categories",
        file=sys.stderr,
    )
    return {"collected": 0, "partial": 2, "empty": 3, "unavailable": 3}[bundle.status]


if __name__ == "__main__":
    raise SystemExit(main())
