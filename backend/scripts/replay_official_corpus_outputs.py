"""Zero-call production Harness replay of immutable, source-pinned corpus outputs.

No provider factory, database or vector client is constructed. Cached evidence
keeps its original retrieval labels, which are historical, not new retrieval.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.harness import ResearchAgent  # noqa: E402
from app.schemas.agent import AskRequest, EvidenceAnchor  # noqa: E402
from app.services.grounded_text import unsupported_numeric_facts  # noqa: E402
from scripts.evaluate_official_all_document_qa import digest  # noqa: E402
from scripts.replay_official_failure_repairs import SavedProvider, SavedRegistry  # noqa: E402


async def replay(report_path, output):
    if output.exists():
        raise ValueError("Never overwrite any preserved cloud or replay result")
    report = json.loads(report_path.read_bytes())
    allowed_kinds = {"official_all_39_one_sample_host_generation",
                     "official_unchanged_corpus_questions_readonly_generation_retest"}
    if (report.get("evaluation_kind") not in allowed_kinds
            or report.get("production_library_modified") is not False
            or report.get("status") != "completed_pending_source_review"
            or report.get("model_calls") != 39 or len(report["cases"]) != 39
            or len({case["filename"] for case in report["cases"]}) != 39):
        raise ValueError("Replay requires the complete unchanged isolated 39-call batch")
    if (report["evaluation_kind"] ==
            "official_unchanged_corpus_questions_readonly_generation_retest"):
        if (report.get("vector_index_modified") is not False
                or report.get("one_generation_per_selected_question") is not True
                or report.get("automatic_retries") is not False
                or len(report.get("selected_filenames", [])) != 39
                or set(report.get("selected_filenames", [])) != {
                    case["filename"] for case in report["cases"]}):
            raise ValueError("Readonly retest replay requires all 39 unchanged selections")
    documents = {doc["filename"]: doc for doc in report["documents"]}
    if len(documents) != 39:
        raise ValueError("Every cached case requires one unambiguous document identity")
    results = []
    for original in report["cases"]:
        doc = documents[original["filename"]]
        raw, request = original["raw_generated"], original["generation_request"]
        if raw is None or request is None:
            raise ValueError("Do not fabricate a missing model response")
        evidence = [EvidenceAnchor.model_validate(item) for item in request["evidence"]]
        if not evidence or any(item.document_id != doc["id"] for item in evidence):
            raise ValueError("Cached evidence is outside the declared paper scope")
        anchors = {item.evidence_id: item for item in evidence}
        response = await ResearchAgent(None, registry=SavedRegistry(evidence),
            provider=SavedProvider(report["model"], raw)).ask(AskRequest(
                question=original["question"], document_ids=[doc["id"]], top_k=18))
        numeric_checks = [{"claim_index": n, "unsupported": unsupported_numeric_facts(
            claim["text"], [anchors[key] for key in claim["evidence_ids"] if key in anchors])}
                          for n, claim in enumerate(raw["claims"])]
        results.append({"filename": original["filename"], "case_id": original["case_id"],
                        "original_machine_status": original["status"], "model_calls": 0,
                        "cached_numeric_checks": numeric_checks,
                        "published_claim_count": len(response.claims),
                        "response": response.model_dump(mode="json")})
    root = Path(__file__).resolve().parents[1]
    outcome = {"evaluation_kind": "offline_39_saved_output_production_harness_replay",
               "model_calls": 0, "new_retrieval_calls": 0,
               "is_new_end_to_end_generation": False,
               "original_report_sha256": digest(report_path),
               "runtime_code_sha256": {str(path.relative_to(root)): digest(path)
                                       for path in [Path(__file__), *sorted(
                                           (root / "app").rglob("*.py"))]},
               "scientific_source_review": "not_assigned_by_replay", "cases": results,
               "summary": {"replayed": len(results), "nonempty_answers": sum(
                   bool(row["published_claim_count"]) for row in results),
                   "incomplete": sum(row["response"]["insufficient_evidence"] for row in results)}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"model_calls": 0, **outcome["summary"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(replay(args.report, args.output))))
