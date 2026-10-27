"""Test the live editor on pinned public premises, NOT a fresh RAG answer.

Replay each saved primary response once into the current production adapter.
Only its one comparison-editor request goes to the provider. No new primary
generation, retrieval, retries, production writes or syntax repairs occur.
"""

import argparse
import copy
import json
import re
from dataclasses import asdict
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_output_features import configure


def selected_cases(source_run):
    source_run = source_run.resolve()
    if not source_run.is_relative_to(TEMP_ROOT) or source_run == TEMP_ROOT:
        raise ValueError("Use an existing isolated official comparison run")
    report_path, calls_path = source_run / "report.json", source_run / "model-calls.json"
    report = json.loads(report_path.read_bytes())
    calls = json.loads(calls_path.read_bytes())
    inputs = saved_inputs(source_run, [row["filename"] for row in report["sources"]])
    cursor, selected = 0, []
    for case in report["cases"]:
        recorded = calls[cursor:cursor + case["cloud_calls"]]
        cursor += case["cloud_calls"]
        if len(recorded) != 2:
            continue
        if [c["request"]["text"]["format"]["name"] for c in recorded] != [
            "grounded_answer", "comparison_source_review",
        ]:
            raise ValueError("Require exactly one saved primary and one saved editor")
        if recorded[0]["status"] != "returned" or not case.get("response", {}).get("evidence"):
            raise ValueError("The primary response or its original evidence is unavailable")
        selected.append((case, recorded[0]["response"], inputs, {
            "report": report_path.relative_to(TEMP_ROOT).as_posix(),
            "report_sha256": digest(report_path), "model_calls_sha256": digest(calls_path),
        }))
    if cursor != len(calls):
        raise ValueError("Saved request count does not match the recorded cases")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        raise ValueError("Explicit public-excerpt cloud authorization required")
    cases = [case for source in args.source_runs for case in selected_cases(source)]
    if not cases:
        raise ValueError("No saved two-stage comparison cases selected")
    root = isolated_output(args.output_dir)
    configure(root)
    from evaluate_official_all_document_qa import source_anchor_valid

    from app.llm.factory import get_llm_provider
    from app.schemas.agent import EvidenceAnchor
    from app.services.chunking import build_chunk_drafts
    from app.services.grounded_text import audit_grounded_claims

    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Actual DeepSeek is required")
    original_post = provider._post_response
    calls = []
    report = {
        "status": "running", "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "scope": "One live editor per pinned saved primary; not new RAG accuracy",
        "primary_generation_replayed": True, "new_primary_generations": 0,
        "new_retrieval_calls": 0, "automatic_retries": 0,
        "production_library_modified": False, "cloud_calls": 0, "cases": [],
    }

    def save():
        report["cloud_calls"] = len(calls)
        write_json(root / "report.json", report)
        write_json(root / "model-calls.json", calls)

    import asyncio

    async def execute():
        for case, primary, inputs, pinned in cases:
            before = len(calls)
            replayed, editor_requested = False, False

            async def post(payload, primary=primary):
                nonlocal replayed, editor_requested
                name = payload["text"]["format"]["name"]
                if name == "grounded_answer" and not replayed:
                    replayed = True
                    return copy.deepcopy(primary)
                if name != "comparison_source_review" or editor_requested or not replayed:
                    raise AssertionError("No additional generation, editor or retry is permitted")
                editor_requested = True
                row = {"request": payload, "status": "started"}
                calls.append(row)
                save()
                try:
                    raw = await original_post(payload)
                    row.update(status="returned", response=raw)
                    return raw
                except Exception as exc:
                    row.update(status="failed", error_type=type(exc).__name__)
                    raise
                finally:
                    save()

            provider._post_response = post
            row = {"case_id": case["case_id"], "pinned_input": pinned, "status": "running"}
            report["cases"].append(row)
            try:
                evidence = [EvidenceAnchor.model_validate(e) for e in case["response"]["evidence"]]
                by_id = {r["row"]["filename"]: r for r in inputs}
                originals = {
                    source["document_id"]: by_id[source["filename"]]
                    for source in json.loads(
                        (TEMP_ROOT / pinned["report"]).read_bytes()
                    )["sources"]
                }
                drafts = {key: build_chunk_drafts(item["parsed"])
                          for key, item in originals.items()}
                valid = all(
                    e.document_id in originals and source_anchor_valid(
                        e, originals[e.document_id]["parsed"], drafts[e.document_id],
                    ) for e in evidence
                )
                if not valid:
                    raise ValueError("Source anchors must pass before the cloud editor is called")
                result = await provider.generate_grounded_answer(
                    case["request"]["question"], evidence,
                )
                accepted, audit = audit_grounded_claims(result.claims, evidence)
                selected = {e.evidence_id: e for e in evidence}
                row.update(
                    result=asdict(result), grounding_audit=audit.model_dump(),
                    checks={
                        "saved_primary_replayed_once": replayed,
                        "exactly_one_live_editor": len(calls) - before == 1,
                        "all_raw_original_anchors_valid": valid,
                        "editor_accepted": result.comparison_review_status in {
                            "supported", "revised",
                        },
                        "comparison_not_withheld": "comparison" not in result.field_rejections,
                        "comparison_cites_two_originals": bool(accepted) and len({
                            selected[key].document_id for key in accepted[0].evidence_ids
                        }) == 2,
                        "no_detectable_claim_conflict": len(accepted) == len(result.claims),
                    },
                )
                row["status"] = "checks_passed_pending_source_review" if all(
                    row["checks"].values()
                ) else "failed"
            except Exception as exc:
                row.update(status="failed", error={
                    "type": type(exc).__name__,
                    "message": re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc))[:700],
                })
            row["cloud_calls"] = len(calls) - before
            save()
            print(f"{case['case_id']}: {row['status']}", flush=True)

    asyncio.run(execute())
    report["status"] = "checks_passed_pending_source_review" if all(
        row["status"] == "checks_passed_pending_source_review" for row in report["cases"]
    ) else "held"
    save()
    print(json.dumps({"status": report["status"], "cases": len(cases),
                      "cloud_calls": len(calls), "new_primary_generations": 0}), flush=True)
    return 0 if report["status"] == "checks_passed_pending_source_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
