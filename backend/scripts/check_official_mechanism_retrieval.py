"""Read-only, zero-LLM retrieval check for a source-pinned explanation passage.

The independent physical page/quote are validated before retrieval, but never
inserted into the query or candidate pool. This is not a generated-answer test.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import fitz
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.rerankers import get_reranker  # noqa: E402
from app.services.chunking import build_chunk_drafts  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from scripts.evaluate_official_all_document_qa import (  # noqa: E402
    digest,
    normalized,
    source_anchor_valid,
    verify_chunks,
)
from scripts.evaluate_official_first_round import (  # noqa: E402
    ReadOnlyOfficialIndex,
    restore_saved_documents,
)
from scripts.trace_official_qa_retrieval import RecordingStage  # noqa: E402


def run(args):
    if args.output.exists():
        raise ValueError("Never overwrite an earlier generation or diagnostic result")
    if not args.probe_quote.strip():
        raise ValueError("The independent explanation probe cannot be empty")
    report = json.loads(args.report.read_bytes())
    if (report.get("evaluation_kind") != "official_all_39_one_sample_host_generation"
            or report.get("status") != "completed_pending_source_review"
            or report.get("production_library_modified") is not False):
        raise ValueError("Require a completed known isolated corpus run")
    cases = [case for case in report["cases"] if case["filename"] == args.filename]
    docs = [doc for doc in report["documents"] if doc["filename"] == args.filename]
    parsed = [doc for doc in json.loads(args.parse_report.read_bytes())["documents"]
              if doc["filename"] == args.filename]
    if len(cases) != 1 or len(docs) != 1 or len(parsed) != 1:
        raise ValueError("The same unambiguous public source is required in every record")
    original, doc, parsed = cases[0], docs[0], parsed[0]
    source = (args.corpus / args.filename).resolve()
    if (Path(args.filename).name != args.filename or source.parent != args.corpus.resolve()
            or digest(source) != doc["sha256"] or parsed["source_sha256"] != doc["sha256"]):
        raise ValueError("Public PDF identity changed")
    artifact = parsed["artifacts_directory"]
    if Path(artifact).name != artifact:
        raise ValueError("Unsafe parser artifact path")
    ast_path = args.parse_report.parent / artifact / "artifacts/parsed.json"
    if digest(ast_path) != doc["ast_sha256"]:
        raise ValueError("The original full parser AST changed")
    ast = json.loads(ast_path.read_bytes())
    with fitz.open(source) as native:
        if (len(native) != doc["pages"] or not 1 <= args.probe_page <= len(native)
                or normalized(args.probe_quote) not in normalized(
                    native[args.probe_page - 1].get_text())):
            raise ValueError("Independent explanation probe is absent from its physical source")
    pattern = r"official_(?:remaining_bge|first_round|all_corpus)_[a-f0-9]{32}"
    if not re.fullmatch(pattern + r"_semantic_[a-f0-9]{10}_1024", doc["collection"]):
        raise ValueError("Never access an arbitrary or production vector namespace")
    if (not settings.vector_search_enabled or settings.embedding_model != "BAAI/bge-m3"
            or settings.reranker_provider != "tei"):
        raise ValueError("Actual BGE vector retrieval and TEI are required")
    settings.vector_collection = doc["collection"].split("_semantic_")[0]
    index = ReadOnlyOfficialIndex()
    if (index.collection_name != doc["collection"]
            or not index.client.collection_exists(index.collection_name)):
        raise ValueError("The original read-only vector namespace is unavailable")
    drafts = build_chunk_drafts(ast)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        restore_saved_documents(session, index, [{**doc, "chunks": len(drafts)}])
        verify_chunks(session, doc["id"], ast)
        reranker = RecordingStage(get_reranker())
        found = HybridRetriever(session, vector_index=index, reranker=reranker).search(
            original["question"], [doc["id"]], 18)
        strong = [item for item in found if item.score >= settings.rag_min_score]
        original_evidence = original["generation_request"]["evidence"]
        def matched(item):
            return (item["page_number"] == args.probe_page
                    and normalized(args.probe_quote) in normalized(item["quote"]))
        checks = {
            "nonempty_bounded_selection": bool(found) and len(found) <= 18,
            "scoped_to_original_document": all(item.document_id == doc["id"] for item in found),
            "hybrid_and_reranked": all(item.recall_mode == "hybrid"
                                       and item.retrieval_mode == "reranked" for item in found),
            "source_anchors_valid": all(source_anchor_valid(item, ast, drafts) for item in found),
            "independent_explanation_found": any(matched(item.model_dump()) for item in found),
            "independent_explanation_in_generation_budget": any(
                matched(item.model_dump()) for item in strong),
        }
        outcome = {
            "evaluation_kind": "readonly_official_explanation_source_probe_not_generation",
            "model_calls": 0, "new_generated_answer": False, "vector_index_modified": False,
            "production_library_modified": False, "question": original["question"],
            "filename": args.filename, "source_sha256": doc["sha256"],
            "original_report_sha256": digest(args.report),
            "independent_probe": {"page": args.probe_page, "quote": args.probe_quote},
            "original_generation_had_explanation_probe": any(map(matched, original_evidence)),
            "checks": checks, "status": "passed" if all(checks.values()) else "failed",
            "rag_min_score": settings.rag_min_score,
            "generation_budget_count": len(strong),
            "current_selected_evidence": [item.model_dump(mode="json") for item in found],
            "actual_usable_evidence": [item.model_dump(mode="json") for item in strong],
            "reranker_stages": reranker.records,
            "code_sha256": {str(path.relative_to(Path(__file__).parents[1])): digest(path)
                            for path in [Path(__file__), *sorted(
                                (Path(__file__).parents[1] / "app").rglob("*.py"))]},
        }
    engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"model_calls": 0, "checks": checks, "status": outcome["status"],
            "original_generation_had_explanation_probe": outcome[
                "original_generation_had_explanation_probe"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("report", "parse-report", "corpus", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--probe-page", type=int, required=True)
    parser.add_argument("--probe-quote", required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments), ensure_ascii=False))
