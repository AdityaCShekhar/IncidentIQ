import argparse
from datetime import UTC, datetime
from pathlib import Path

from incidentiq.core.config import Settings
from incidentiq.db.session import build_engine
from incidentiq.evaluation.dataset import load_dataset
from incidentiq.retrieval.evaluation import run_retrieval_evaluation, write_retrieval_report
from incidentiq.retrieval.knowledge import (
    CORPUS_VERSION,
    FastEmbedModel,
    HashNgramEmbedder,
    PostgresKnowledgeStore,
    Retriever,
    load_corpus,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index and evaluate IncidentIQ knowledge retrieval"
    )
    parser.add_argument("--corpus", type=Path, default=Path("knowledge"))
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument("--split", choices=("dev", "held_out"), default="dev")
    parser.add_argument("--final-report", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports/retrieval-dev"))
    parser.add_argument(
        "--reindex", action="store_true", help="replace the PostgreSQL knowledge index"
    )
    parser.add_argument("--backend", choices=("memory", "postgres"), default="memory")
    parser.add_argument(
        "--embedding-model",
        choices=("hash-ngram-v1", "BAAI/bge-small-en-v1.5"),
        default="hash-ngram-v1",
    )
    args = parser.parse_args()
    if args.split == "held_out" and not args.final_report:
        parser.error("--split held_out requires --final-report")
    try:
        chunks = load_corpus(args.corpus)
        embedder = (
            FastEmbedModel(args.embedding_model)
            if args.embedding_model != "hash-ngram-v1"
            else HashNgramEmbedder()
        )
        retriever = Retriever(chunks, embedder)
        if args.backend == "postgres" or args.reindex:
            store = PostgresKnowledgeStore(build_engine(Settings()), embedder)
            store.chunks = chunks
            if args.reindex:
                store.reindex(chunks)
            retriever = store
        cases = load_dataset(args.dataset, split=args.split)
        metadata, results = run_retrieval_evaluation(cases, retriever)
        metadata.update(
            {
                "corpus_version": CORPUS_VERSION,
                "chunk_count": len(chunks),
                "dataset_version": "fixture-v1",
                "split": args.split,
                "generated_at": datetime.now(UTC).isoformat(),
                "embedding_model": embedder.model_name,
                "embedding_library": (
                    "fastembed/onnxruntime" if isinstance(embedder, FastEmbedModel) else "builtin"
                ),
                "embedding_library_version": embedder.library_version,
                "backend": args.backend,
            }
        )
        write_retrieval_report(args.output_dir, metadata, results)
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(
            1,
            "Retrieval evaluation failed: check corpus, dataset, database and output path.\n",
        )
    print(f"Wrote retrieval comparison for {len(results)} cases to {args.output_dir}")
    return 0
