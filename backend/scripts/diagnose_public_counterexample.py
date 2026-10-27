"""One diagnostic generation from SHA-bound saved public evidence, not fresh retrieval.

Preserve the unchecked output locally so a guard failure can be reproduced without
another cloud call. Never publish raw claims or access the production database.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.harness import ResearchAgent  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.schemas.agent import AskRequest, EvidenceAnchor  # noqa: E402
from app.services.claim_semantics import claim_semantic_conflicts  # noqa: E402
from app.services.grounded_text import unsupported_numeric_facts  # noqa: E402
from scripts.evaluate_official_first_round import OneAttemptProvider  # noqa: E402
from scripts.inspect_runtime_source import inspect_source  # noqa: E402
from scripts.official_functional_inputs import digest, isolated_output, write_json  # noqa: E402
from scripts.replay_official_failure_repairs import SavedRegistry  # noqa: E402
from scripts.verify_running_qa_counterexamples import checks  # noqa: E402


def verified_inputs(policy, report, snapshot, source_sha, case_id, dataset_sha):
    if not (
        source_sha == policy["source_sha256"] == snapshot["document"]["sha256"]
        and report["source_sha256"] == source_sha
        and report["dataset_sha256"] == dataset_sha
        and report["document_id"] == snapshot["document"]["id"]
    ):
        raise ValueError("Saved evidence is not bound to the declared public source")
    cases = [case for case in policy["cases"] if case["case_id"] == case_id]
    rows = [row for row in report["cases"] if row["case_id"] == case_id]
    if len(cases) != 1 or len(rows) != 1 or rows[0]["question"] != cases[0]["question"]:
        raise ValueError("Exactly one unchanged declared question is required")
    chunks = {chunk["id"]: chunk for chunk in snapshot["chunks"]}
    evidence = [EvidenceAnchor.model_validate(item) for item in rows[0]["response"]["evidence"]]
    if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
        raise ValueError("Saved evidence must be nonempty with unique IDs")
    for item in evidence:
        chunk = chunks.get(item.chunk_id)
        if not chunk or (
            item.document_id != report["document_id"]
            or chunk["document_id"] != item.document_id
            or item.quote != chunk["text"]
            or item.page_number != chunk["page_number"]
            or item.block_ids != json.loads(chunk["block_ids_json"])
            or item.section != chunk["section"]
            or item.document_title != snapshot["document"]["title"]
            or item.recall_mode != "hybrid" or item.retrieval_mode != "reranked"
        ):
            raise ValueError("Saved quote or anchor differs from the public chunk snapshot")
    return cases[0], evidence


async def run(args):
    if not args.allow_cloud:
        raise ValueError("Explicit public-excerpt cloud authorization required")
    policy = json.loads(args.dataset.read_text(encoding="utf-8"))
    source = (args.corpus / policy["source_filename"]).resolve()
    if source.parent != args.corpus.resolve():
        raise ValueError("Unsafe public input path")
    saved = json.loads(args.saved_report.read_text(encoding="utf-8"))
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    case, evidence = verified_inputs(
        policy, saved, snapshot, digest(source), args.case_id, digest(args.dataset)
    )
    delegate = get_llm_provider()
    if delegate.name != "deepseek":
        raise ValueError("This diagnostic requires the configured DeepSeek provider")
    output = isolated_output(args.output)
    provider = OneAttemptProvider(delegate, require_full_retrieval=True)
    report = {
        "status": "running", "case_id": args.case_id, "question": case["question"],
        "evaluation_kind": "saved_public_evidence_new_generation_diagnostic",
        "dataset_sha256": digest(args.dataset), "source_sha256": digest(source),
        "saved_report_sha256": digest(args.saved_report), "snapshot_sha256": digest(args.snapshot),
        "fresh_retrieval": False, "database_modified": False, "vector_index_modified": False,
        "automatic_retries": 0, "model_calls": 0,
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
    }
    write_json(output / "report.json", report)
    try:
        response = await ResearchAgent(
            None, registry=SavedRegistry(evidence), provider=provider
        ).ask(AskRequest(question=case["question"], document_ids=[saved["document_id"]], top_k=18))
        report["response"] = response.model_dump(mode="json")
        report["checks"] = checks(case, report["response"], saved["document_id"])
        anchors = {item.evidence_id: item for item in evidence}
        report["raw_claim_checks"] = [
            {
                "claim_index": index,
                "unsupported_numbers": unsupported_numeric_facts(
                    claim["text"], [anchors[key] for key in claim["evidence_ids"] if key in anchors]
                ),
                "semantic_conflicts": claim_semantic_conflicts(
                    claim["text"], [anchors[key] for key in claim["evidence_ids"] if key in anchors],
                    evidence,
                ),
            }
            for index, claim in enumerate((provider.raw or {}).get("claims", []))
        ]
        report["status"] = "completed_diagnostic_pending_source_review"
    except Exception as exc:
        report["status"] = "stopped_runtime_error"
        report["error_type"] = type(exc).__name__
    finally:
        report.update(model_calls=provider.calls, raw_generated=provider.raw,
                      raw_provider_output=provider.raw_provider_output, generation_request=provider.request)
        write_json(output / "report.json", report)
    print(json.dumps({"status": report["status"], "model_calls": report["model_calls"],
                      "checks": report.get("checks"), "raw_claim_checks": report.get("raw_claim_checks")},
                     ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "corpus", "saved-report", "snapshot", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    result = asyncio.run(run(parser.parse_args()))
    raise SystemExit(0 if result["status"].startswith("completed") else 1)
