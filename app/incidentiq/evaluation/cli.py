import argparse
from pathlib import Path

from incidentiq.agent.ollama import OllamaAdapter
from incidentiq.agent.prompts import SUMMARY_PROMPT_VERSION
from incidentiq.agent.workflows import run_agent, run_rag
from incidentiq.baselines.rules import RULES_VERSION, run_rules
from incidentiq.evaluation.benchmark import evaluate, write_comparison, write_reports
from incidentiq.evaluation.dataset import load_dataset
from incidentiq.retrieval.knowledge import Retriever, load_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark a diagnosis strategy on frozen cases")
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument("--split", choices=("dev", "held_out"), default="dev")
    parser.add_argument(
        "--final-report", action="store_true",
        help="explicitly authorize scoring the reserved held-out split",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--strategy", choices=("rules", "llm_summary", "rag", "agent", "all"), default="rules"
    )
    parser.add_argument("--model", help="required Ollama model name for model-backed strategies")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--knowledge-dir", type=Path, default=Path("knowledge"))
    args = parser.parse_args()
    if args.split == "held_out" and not args.final_report:
        parser.error("--split held_out requires --final-report")
    selected = ("rules", "llm_summary", "rag", "agent") if args.strategy == "all" else (args.strategy,)
    if any(strategy != "rules" for strategy in selected) and not args.model:
        parser.error("model-backed strategies require --model")
    try:
        cases = load_dataset(args.dataset, split=args.split)
        model_strategies = set(selected) & {"llm_summary", "rag", "agent"}
        adapter = None
        retriever = None
        if model_strategies:
            adapter = OllamaAdapter(
                args.model, base_url=args.base_url, timeout_seconds=args.timeout
            )
        if set(selected) & {"rag", "agent"}:
            retriever = Retriever(load_corpus(args.knowledge_dir))
        strategies = {
            "rules": (run_rules, RULES_VERSION),
            "llm_summary": (adapter.diagnose if adapter else None, SUMMARY_PROMPT_VERSION),
            "rag": (
                (lambda bundle: run_rag(bundle, adapter, retriever)) if adapter and retriever else None,
                "rag-v2",
            ),
            "agent": (
                (lambda bundle: run_agent(bundle, adapter, retriever)) if adapter and retriever else None,
                "agent-v2",
            ),
        }
        base_output = args.output_dir or Path("evals/reports") / f"{args.split}-comparison"
        comparison_reports = {}
        results = {}
        for name in selected:
            strategy, version = strategies[name]
            metadata, rows = evaluate(
                cases,
                strategy=strategy,
                strategy_name=name,
                strategy_version=version,
                split=args.split,
            )
            if name in model_strategies:
                metadata["model"] = args.model
                metadata["base_url"] = args.base_url
                metadata["timeout_seconds"] = args.timeout
            output_dir = (
                base_output / name if args.strategy == "all"
                else args.output_dir or Path("evals/reports") / f"{args.split}-{name}"
            )
            write_reports(output_dir, metadata, rows)
            comparison_reports[name] = metadata
            results[name] = (metadata, rows, output_dir)
        if args.strategy == "all":
            write_comparison(base_output, comparison_reports)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        parser.exit(
            1,
            "Benchmark failed: check the frozen dataset, strategy outputs and report path.\n",
        )
    for name, (metadata, rows, output_dir) in results.items():
        print(
            f"Wrote {len(rows)} {args.split} {name} case results to {output_dir}; "
            f"accuracy={metadata['metrics']['accuracy']:.3f}, "
            f"macro_f1={metadata['metrics']['macro_f1']:.3f}, "
            f"failures={metadata['metrics']['failed_cases']}"
        )
    if args.strategy == "all":
        print(f"Wrote cross-strategy comparison to {base_output}")
    return 0
