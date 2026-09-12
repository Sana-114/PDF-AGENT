import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.evaluation import RetrievalEvaluationSet, evaluate_retrieval  # noqa: E402
from app.services.retrieval import LexicalRetriever  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate grounded retrieval against a versioned JSON dataset."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--retriever",
        choices=("configured", "lexical"),
        default="configured",
        help="Use configured hybrid retrieval or the deterministic lexical baseline.",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only status and aggregate metrics while keeping the full output file.",
    )
    args = parser.parse_args()

    dataset = RetrievalEvaluationSet.model_validate_json(
        args.dataset.read_text(encoding="utf-8")
    )
    init_db()
    with SessionLocal() as session:
        retriever = LexicalRetriever(session) if args.retriever == "lexical" else None
        report = evaluate_retrieval(session, dataset, retriever=retriever)
    report["retriever"] = args.retriever

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.summary_only:
        print(
            json.dumps(
                {
                    "dataset_id": report["dataset_id"],
                    "retriever": report["retriever"],
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


if __name__ == "__main__":
    main()
