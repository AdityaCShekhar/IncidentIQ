import argparse
from pathlib import Path

from incidentiq.baselines.rules import RULES_VERSION
from incidentiq.evaluation.benchmark import evaluate, write_reports
from incidentiq.evaluation.dataset import load_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark a diagnosis strategy on frozen cases")
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument("--split", choices=("dev", "held_out"), default="dev")
    parser.add_argument(
        "--final-report", action="store_true",
        help="explicitly authorize scoring the reserved held-out split",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports/dev-rules"))
    args = parser.parse_args()
    if args.split == "held_out" and not args.final_report:
        parser.error("--split held_out requires --final-report")
    try:
        cases = load_dataset(args.dataset, split=args.split)
        metadata, rows = evaluate(cases, split=args.split)
        write_reports(args.output_dir, metadata, rows)
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(1, "Benchmark failed: check the frozen dataset, strategy outputs and report path.\n")
    print(
        f"Wrote {len(rows)} {args.split} case results to {args.output_dir}; "
        f"accuracy={metadata['metrics']['accuracy']:.3f}, "
        f"macro_f1={metadata['metrics']['macro_f1']:.3f}"
    )
    return 0
