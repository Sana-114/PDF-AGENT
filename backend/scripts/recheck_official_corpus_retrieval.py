"""Recheck all 39 immutable QA source probes with current read-only BGE retrieval.

This never constructs a language-model provider, reparses a PDF, writes vectors,
or reclassifies the historical generated answers. Each original query runs once.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.services.chunking import build_chunk_drafts  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from scripts.evaluate_official_all_document_qa import (  # noqa: E402
    collect_inputs,
    digest,
    normalized,
    source_anchor_valid,
    verify_chunks,
)
from scripts.evaluate_official_first_round import (  # noqa: E402
    ReadOnlyOfficialIndex,
    restore_saved_documents,
)


def run(args):
    if args.output.exists():
        raise ValueError("Never overwrite an original or current validation result")
    report = json.loads(args.report.read_bytes())
    if (report.get("evaluation_kind") != "official_all_39_one_sample_host_generation"
            or report.get("status") != "completed_pending_source_review"
            or report.get("production_library_modified") is not False
            or report["dataset_sha256"] != digest(args.answers)):
        raise ValueError("Require unchanged question/source inputs from a known isolated run")
    if (not settings.vector_search_enabled or settings.embedding_model != "BAAI/bge-m3"
            or settings.reranker_provider != "tei"):
        raise ValueError("Require actual configured BGE retrieval and TEI")
    inputs = collect_inputs(args.corpus, args.manifest, args.probes,
                            args.answers, args.parse_reports)
    saved = {doc["filename"]: doc for doc in report["documents"]}
    original_cases = {case["filename"]: case for case in report["cases"]}
    if len(saved) != 39 or len(original_cases) != 39:
        raise ValueError("Require exactly one original case and source identity per file")
    if args.filenames:
        if len(set(args.filenames)) != len(args.filenames) or set(args.filenames) - set(saved):
            raise ValueError("Subset filenames must be unique original public files")
        inputs = [probe for probe in inputs if probe["filename"] in set(args.filenames)]
    outcome = {"evaluation_kind": "readonly_current_39_original_source_probes_not_generation",
               "model_calls": 0, "parser_calls": 0, "automatic_retries": False,
               "production_library_modified": False, "vector_index_modified": False,
               "new_generated_answer": False, "original_report_sha256": digest(args.report),
               "code_sha256": {str(path.relative_to(Path(__file__).parents[1])): digest(path)
                               for path in [Path(__file__), *sorted(
                                   (Path(__file__).parents[1] / "app").rglob("*.py"))]},
               "cases": []}
    # Create the output before local network work so even interrupted/failed
    # runs are retained. Only this new run's own file is updated incrementally.
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        args.output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")

    checkpoint()
    for n, probe in enumerate(inputs, 1):
        doc = saved[probe["filename"]]
        original = original_cases[probe["filename"]]
        if (doc["sha256"] != probe["sha256"] or doc["ast_sha256"] != probe["ast_sha256"]
                or original["question"] != probe["question"]):
            raise ValueError("Original source, AST or question changed")
        collection = doc["collection"]
        pattern = r"official_(?:remaining_bge|first_round|all_corpus)_[a-f0-9]{32}"
        if not re.fullmatch(pattern + r"_semantic_[a-f0-9]{10}_1024", collection):
            raise ValueError("Never access an arbitrary or production vector namespace")
        settings.vector_collection = collection.split("_semantic_")[0]
        index = ReadOnlyOfficialIndex()
        if index.collection_name != collection or not index.client.collection_exists(collection):
            raise ValueError("An unchanged saved vector namespace is unavailable")
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                drafts = build_chunk_drafts(probe["ast"])
                restore_saved_documents(session, index, [{**doc, "chunks": len(drafts)}])
                verify_chunks(session, doc["id"], probe["ast"])
                evidence = HybridRetriever(session, vector_index=index).search(
                    original["question"], [doc["id"]], 18)
                usable = [item for item in evidence if item.score >= settings.rag_min_score]
                checks = {
                    "bounded_nonempty_usable_evidence": bool(usable) and len(evidence) <= 18,
                    "scoped": all(item.document_id == doc["id"] for item in evidence),
                    "hybrid_and_reranked": all(item.recall_mode == "hybrid"
                                               and item.retrieval_mode == "reranked"
                                               for item in evidence),
                    "usable_source_anchors_valid": all(
                        source_anchor_valid(item, probe["ast"], drafts) for item in usable),
                    "independent_probe_in_usable_budget": any(
                        item.page_number == probe["page"] and normalized(probe["quote"])
                        in normalized(item.quote) for item in usable),
                }
                row = {"filename": doc["filename"], "question": original["question"],
                       "source_sha256": doc["sha256"], "checks": checks,
                       "status": "passed" if all(checks.values()) else "failed",
                       "anchor_checks": [{"evidence_id": item.evidence_id,
                                          "page": item.page_number, "score": item.score,
                                          "valid": source_anchor_valid(item, probe["ast"], drafts)}
                                         for item in evidence],
                       "unusable_invalid_candidate_count": sum(
                           item.score < settings.rag_min_score
                           and not source_anchor_valid(item, probe["ast"], drafts)
                           for item in evidence),
                       "actual_usable_evidence": [item.model_dump(mode="json") for item in usable]}
                outcome["cases"].append(row)
                checkpoint()
                print(json.dumps({"progress": n, "filename": doc["filename"],
                                  "status": row["status"]}), flush=True)
        finally:
            engine.dispose()
    outcome["summary"] = {"selected": len(inputs), "executed": len(outcome["cases"]),
                          "passed": sum(row["status"] == "passed" for row in outcome["cases"]),
                          "failed": sum(row["status"] == "failed" for row in outcome["cases"])}
    outcome["status"] = "passed" if outcome["summary"]["passed"] == len(inputs) else "failed"
    checkpoint()
    return {"model_calls": 0, **outcome["summary"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "manifest", "probes", "answers", "report", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--parse-reports", type=Path, nargs="+", required=True)
    parser.add_argument("--filenames", nargs="+",
                        help="Optional affected-file subset, no new questions")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments)))
