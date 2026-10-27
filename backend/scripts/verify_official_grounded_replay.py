"""Fresh DeepSeek answers on pinned archived evidence, NOT a fresh BGE RAG test.

Replays the four unchanged public questions through the current real harness.
Only evidence retrieval is replaced with a scoped archive reader. No old answer
or provider response is replayed, no automatic retry or production DB is used.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_output_features import configure


class ArchivedEvidenceRegistry:
    def __init__(self, evidence, document_ids, reads):
        self.evidence, self.scope, self.reads = evidence, set(document_ids), reads

    async def execute(self, name, args, context):
        if name != "search_evidence" or not args["document_ids"]:
            raise AssertionError("Only explicitly scoped archive reads are permitted")
        scope = set(args["document_ids"])
        if not scope.issubset(self.scope):
            raise AssertionError("Archive scope expanded")
        self.reads.append({"document_ids": sorted(scope), "not_live_retrieval": True})
        return [e.model_copy(deep=True) for e in self.evidence if e.document_id in scope]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        raise ValueError("Public-excerpt cloud authorization required")
    source = args.source_run.resolve()
    if not source.is_relative_to(TEMP_ROOT) or source == TEMP_ROOT:
        raise ValueError("Read only a dedicated archived official run")
    source_report = source / "report.json"
    archived = json.loads(source_report.read_bytes())
    inputs = saved_inputs(source, [r["filename"] for r in archived["sources"]])
    if len(archived["cases"]) != 4 or len(inputs) != 5:
        raise ValueError("Require the complete four-question, five-source fixture")
    root = isolated_output(args.output_dir)
    configure(root)

    from evaluate_official_all_document_qa import source_anchor_valid
    from verify_official_candidate_qa import comparison_checks, policy_checks

    from app.agent.harness import ResearchAgent
    from app.llm.factory import get_llm_provider
    from app.schemas.agent import AskRequest, EvidenceAnchor
    from app.services.chunking import build_chunk_drafts

    by_name = {item["row"]["filename"]: item for item in inputs}
    originals = {r["document_id"]: by_name[r["filename"]] for r in archived["sources"]}
    drafts = {key: build_chunk_drafts(item["parsed"]) for key, item in originals.items()}
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Actual DeepSeek is required")
    report = {
        "status": "running", "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "scope": "Fresh harness generation on pinned ARCHIVED evidence; not fresh retrieval",
        "source_report": source_report.relative_to(TEMP_ROOT).as_posix(),
        "source_report_sha256": digest(source_report), "sources": archived["sources"],
        "new_retrieval_calls": 0, "new_vector_queries": 0, "new_embedding_calls": 0,
        "new_reranker_calls": 0, "old_answer_replayed": False, "automatic_retries": 0,
        "production_library_modified": False, "full_rag_accuracy_assigned": False,
        "scientific_review": "pending", "cloud_calls": 0, "cases": [],
    }
    calls = []

    def save():
        report["cloud_calls"] = len(calls)
        write_json(root / "report.json", report)
        write_json(root / "model-calls.json", calls)

    original_post = provider._post_response

    async def execute():
        for saved in archived["cases"]:
            evidence = [EvidenceAnchor.model_validate(e) for e in saved["response"]["evidence"]]
            if not evidence or not all(
                e.document_id in originals and source_anchor_valid(
                    e, originals[e.document_id]["parsed"], drafts[e.document_id],
                ) for e in evidence
            ):
                raise ValueError("Verify archived source anchors before any cloud call")
            before, request_names = len(calls), []
            row = {"case_id": saved["case_id"], "request": saved["request"],
                   "status": "running", "archive_reads": []}
            report["cases"].append(row)

            async def post(payload, request_names=request_names):
                name = payload["text"]["format"]["name"]
                expected = "grounded_answer" if not request_names else "comparison_source_review"
                if name != expected or len(request_names) >= 2:
                    raise AssertionError("No repeated generation, editor, or hidden retry")
                request_names.append(name)
                call = {"request": payload, "status": "started"}
                calls.append(call)
                save()
                try:
                    raw = await original_post(payload)
                    call.update(status="returned", response=raw)
                    return raw
                except Exception as exc:
                    call.update(status="failed", error_type=type(exc).__name__)
                    raise
                finally:
                    save()

            provider._post_response = post
            try:
                registry = ArchivedEvidenceRegistry(evidence, saved["request"]["document_ids"],
                                                    row["archive_reads"])
                response = await ResearchAgent(None, registry=registry,
                    provider=provider).ask(AskRequest.model_validate(saved["request"]))
                payload = response.model_dump(mode="json")
                row["response"] = payload
                if saved["suite"] == "policy":
                    checks = policy_checks(payload, saved["request"]["document_ids"],
                        counterexample="reject-global" in saved["case_id"])
                    checks.pop("published_sources_scoped_and_hybrid_reranked", None)
                elif saved["case_id"] == "lora-qlora-mechanism":
                    checks = comparison_checks(payload, saved["request"]["document_ids"])
                    checks.pop("all_used_anchors_hybrid_reranked", None)
                else:
                    checks = {
                        "configured_deepseek": payload["provider"] == "deepseek",
                        "explicit_insufficient_refusal": payload["insufficient_evidence"],
                        "no_seed_integer_invented": not re.search(
                            r"(?:seed|种子).{0,15}(?:是|为|=|is)\s*\d+", payload["answer"], re.I),
                    }
                checks["all_original_source_anchors_valid"] = bool(response.evidence) and all(
                    e.document_id in originals and source_anchor_valid(
                        e, originals[e.document_id]["parsed"], drafts[e.document_id],
                    ) for e in response.evidence
                )
                checks["one_fresh_primary_no_retry"] = request_names.count("grounded_answer") == 1
                row.update(checks=checks, status="machine_passed_pending_source_review"
                           if all(checks.values()) else "failed")
            except Exception as exc:
                row.update(status="failed", error={"type": type(exc).__name__,
                    "message": re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc))[:700]})
            row["cloud_calls"] = len(calls) - before
            save()
            print(f"{row['case_id']}: {row['status']}", flush=True)

    try:
        asyncio.run(execute())
        report["status"] = "complete_pending_source_review"
    except Exception as exc:
        report.update(status="failed", error={"type": type(exc).__name__})
    finally:
        save()
    print(json.dumps({"status": report["status"], "cases": len(report["cases"]),
                      "cloud_calls": len(calls), "new_retrieval_calls": 0}), flush=True)
    return 0 if len(report["cases"]) == 4 and all(
        c["status"] == "machine_passed_pending_source_review" for c in report["cases"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
