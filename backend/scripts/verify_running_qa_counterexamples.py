"""One attempt per fixed public-PDF counterexample through the actual Docker API."""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx
import pymupdf
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.official_functional_inputs import digest, isolated_output, write_json  # noqa: E402

from app.schemas.agent import AnswerClaim
from app.services.grounded_text import only_source_limitations


def checks(case: dict, payload: dict, document_id: str) -> dict:
    anchors = {item["evidence_id"]: item for item in payload["evidence"]}
    used = {key for claim in payload["claims"] for key in claim["evidence_ids"]}
    result = {
        "configured_deepseek": payload["provider"] == "deepseek",
        "no_out_of_scope_published_source": all(
            key in anchors and anchors[key]["document_id"] == document_id for key in used
        ),
    }
    if case["expectation"] == "refuse_without_inventing_seed":
        result["refuses_without_published_seed_claim"] = payload["insufficient_evidence"] and (
            not payload["claims"]
            or only_source_limitations(
                [AnswerClaim.model_validate(claim) for claim in payload["claims"]]
            )
            or any(
                step["skill"] == "audit_answer_completeness"
                and step["status"] == "incomplete"
                and "随机种子" in step["summary"]
                for step in payload["trace"]
            )
        )
        result["no_positive_seed_number"] = not any(
            re.search(
                r"(?:随机(?:数)?种子|seed)[^。;；\n]{0,15}"
                r"(?:设(?:置)?为|等于|=|\bis\b)[^。;；\n]{0,5}\d",
                claim["text"],
                re.I,
            )
            for claim in payload["claims"]
        )
    else:
        result["correct_value_with_original_page"] = any(
            case["required_correct_value"] in claim["text"]
            and any(
                anchors[key]["page_number"] == case["source_page"]
                for key in claim["evidence_ids"]
                if key in anchors
            )
            for claim in payload["claims"]
        )
        result["not_refused_instead_of_correction"] = not payload["insufficient_evidence"]
        result["used_sources_hybrid_reranked"] = bool(used) and all(
            anchors[key]["recall_mode"] == "hybrid" and anchors[key]["retrieval_mode"] == "reranked"
            for key in used
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        raise ValueError("Explicit public excerpt verification authorization required")
    policy = json.loads(args.dataset.read_text(encoding="utf-8"))
    source = (args.corpus / policy["source_filename"]).resolve()
    if source.parent != args.corpus.resolve() or digest(source) != policy["source_sha256"]:
        raise ValueError("Public PDF identity mismatch")
    with pymupdf.open(source) as pdf:
        text = "\n".join(page.get_text() for page in pdf)
        for case in policy["cases"]:
            if case.get("source_absence_regex") and re.search(case["source_absence_regex"], text):
                raise ValueError("Absence premise is invalid; do not call model")
    output = isolated_output(args.output)
    report = {
        "status": "running",
        "dataset_sha256": digest(args.dataset),
        "source_sha256": policy["source_sha256"],
        "automatic_retries": 0,
        "generation_requests": 0,
        "traced_model_calls": 0,
        "production_library_modified": False,
        "cases": [],
    }
    try:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180, trust_env=False) as client:
            library = client.get("/api/v1/documents", params={"limit": 200})
            library.raise_for_status()
            matches = [
                row
                for row in library.json()["items"]
                if (row["sha256"] == policy["source_sha256"] and row["status"] == "ready")
            ]
            if len(matches) != 1 or matches[0]["parser_version"] != "1.26.4-layout98":
                raise ValueError("One up-to-date SHA-verified public document required")
            document_id = matches[0]["id"]
            report["document_id"] = document_id
            for case in policy["cases"]:
                row = {"case_id": case["case_id"], "question": case["question"]}
                report["generation_requests"] += 1
                write_json(output / "report.json", report)
                response = client.post(
                    "/api/v1/agent/ask",
                    json={
                        "question": case["question"],
                        "document_ids": [document_id],
                        "top_k": 18,
                    },
                )
                row["http_status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                row["response"] = payload
                row["checks"] = checks(case, payload, document_id)
                row["status"] = (
                    "machine_passed_pending_source_review"
                    if all(row["checks"].values())
                    else "failed"
                )
                report["traced_model_calls"] += sum(
                    step["skill"] == "llm.generate_grounded_answer" for step in payload["trace"]
                )
                report["cases"].append(row)
                write_json(output / "report.json", report)
                print(json.dumps({"case_id": row["case_id"], "status": row["status"]}), flush=True)
        report["status"] = "completed_pending_source_review"
    except Exception as exc:
        report["status"] = "stopped_runtime_error"
        report["error_type"] = type(exc).__name__
    finally:
        report["summary"] = {
            "executed": len(report["cases"]),
            "machine_passed": sum(
                row["status"].startswith("machine_passed") for row in report["cases"]
            ),
        }
        write_json(output / "report.json", report)
    print(json.dumps(report["summary"]), flush=True)
    return 0 if report["summary"] == {"executed": 3, "machine_passed": 3} else 1


if __name__ == "__main__":
    raise SystemExit(main())
