import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.evaluation import (  # noqa: E402
    GroundedRAGEvaluationSet,
    evaluate_grounded_rag,
)
from app.services.retrieval import LexicalRetriever  # noqa: E402


async def run() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the complete retrieval, LLM, citation, and refusal pipeline."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--retriever",
        choices=("configured", "lexical"),
        default="configured",
        help="Use the configured hybrid chain or the deterministic lexical baseline.",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--require-retrieval-mode",
        choices=("lexical", "vector", "hybrid", "reranked"),
        help="Fail if any returned evidence bypasses the required retrieval stage.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print aggregate metrics while retaining full case details in --output.",
    )
    args = parser.parse_args()

    dataset = GroundedRAGEvaluationSet.model_validate_json(
        args.dataset.read_text(encoding="utf-8")
    )
    init_db()
    with SessionLocal() as session:
        retriever = LexicalRetriever(session) if args.retriever == "lexical" else None
        report = await evaluate_grounded_rag(
            session,
            dataset,
            retriever=retriever,
            required_retrieval_mode=args.require_retrieval_mode,
        )
    report["retriever"] = args.retriever

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.summary_only:
        print(
            json.dumps(
                {
                    "dataset_id": report["dataset_id"],
                    "provider": report["provider"],
                    "model": report["model"],
                    "retriever": report["retriever"],
                    "required_retrieval_mode": report["required_retrieval_mode"],
                    "status": report["status"],
                    "threshold_failures": report["threshold_failures"],
                    "metrics": report["metrics"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.strict and report["status"] != "passed":
        raise SystemExit(1)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
