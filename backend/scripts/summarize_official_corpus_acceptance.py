"""Export bounded public counts and source identities, never full model payloads.

This offline aggregator does not call a model or retrieval service. It keeps the
original generation score, explicit manual core review and saved-output replay
separate. Existing evidence files must never be overwritten.
"""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ARTIFACTS = (
    "sources-1/manifest.json",
    "parse-32-final/report.json",
    "parse-core-7-final/report.json",
    "version-pair-final.json",
    "book-resume-final.json",
    "targeted-3-1/report.json",
    "targeted-3-review-1.json",
    "targeted-3-guard-replay-final.json",
    "qa-39-1/report.json",
    "qa-39-2/report.json",
    "qa-39-2/source-gold.json",
    "qa-core-review-1.json",
    "qa-replay-final.json",
    "gold-pages-1/sources.json",
    "qa-cited-pages-1/sources.json",
    "full-unit-1.xml",
    "trillion-repair-tests.xml",
    "full-unit-delivery-final.xml",
)
PRODUCTION_FILES = (
    "app/agent/harness.py", "app/llm/openai_responses.py",
    "app/parsers/people.py", "app/parsers/pymupdf_parser.py",
    "app/services/answer_completeness.py", "app/services/grounded_text.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def export(root, output):
    if output.exists():
        raise ValueError("Never overwrite an existing acceptance snapshot")
    qa = read_json(root / "qa-39-2/report.json")
    core = read_json(root / "qa-core-review-1.json")
    replay = read_json(root / "qa-replay-final.json")
    targeted = read_json(root / "targeted-3-1/report.json")
    targeted_review = read_json(root / "targeted-3-review-1.json")
    targeted_replay = read_json(root / "targeted-3-guard-replay-final.json")
    report_sha = digest(root / "qa-39-2/report.json")
    if (core["original_report_sha256"] != report_sha
            or replay["original_report_sha256"] != report_sha
            or core["original_source_gold_sha256"] != qa["source_gold_sha256"]
            or digest(root / "qa-39-2/source-gold.json") != qa["source_gold_sha256"]):
        raise ValueError("Core review, independent source and replay must refer to the same run")
    targeted_sha = digest(root / "targeted-3-1/report.json")
    if (targeted_review["original_report_sha256"] != targeted_sha
            or targeted_replay["original_report_sha256"] != targeted_sha
            or qa["prior_calls_report_sha256"] != targeted_sha
            or qa["production_library_modified"] is not False
            or qa["automatic_retries"] is not False
            or targeted["automatic_retries"] is not False):
        raise ValueError("The three-case run or declared isolation changed")
    if (qa["model_calls"] != 39 or targeted["model_calls"] != 3
            or qa["prior_authorized_calls"] != 3
            or qa["max_total_authorized_calls"] != 42
            or any(row["model_calls"] != 1 for row in qa["cases"] + targeted["cases"])
            or replay["model_calls"] != 0 or replay["new_retrieval_calls"] != 0
            or replay["is_new_end_to_end_generation"] is not False
            or targeted_replay["model_calls"] != 0
            or targeted_replay["is_new_end_to_end_generation"] is not False):
        raise ValueError("Cloud generation and zero-call replay cannot be mixed")
    rows = core["files"]
    if (len(rows) != 39 or len({row["filename"] for row in rows}) != 39
            or {row["filename"] for row in rows} != {row["filename"] for row in qa["cases"]}
            or any(row["whole_answer_reviewed"] is not False for row in rows)
            or core["whole_answer_acceptance"] != "not_certified"):
        raise ValueError("Require exactly 39 bounded core reviews, not whole-answer acceptance")
    cases = {case["filename"]: case for case in qa["cases"]}
    for row in rows:
        original = cases[row["filename"]]
        if (row["original_machine_status"] != original["status"]
                or row["original_machine_failures"] != original["failures"]):
            raise ValueError("The core review cannot change an original machine verdict")
    recomputed = {
        "files": len(rows), "pages": sum(row["pages"] for row in rows),
        "foundation_passed": sum(row["parser_gate_passed"] for row in rows),
        "independent_source_retrieved": sum(
            row["independent_source_in_original_request"] for row in rows),
        "original_machine_passed": sum(row["original_machine_status"] ==
                                       "machine_passed_pending_source_review" for row in rows),
        "requested_core_accepted": sum(row["requested_core_accepted"] for row in rows),
        "additional_quality_holds": sum(bool(row["quality_holds"]) for row in rows),
    }
    if core["summary"] != recomputed:
        raise ValueError("The reported totals disagree with the 39 explicit file results")
    parsed = []
    for name in ("parse-32-final/report.json", "parse-core-7-final/report.json"):
        parsed.extend(read_json(root / name)["documents"])
    source_rows = {row["filename"]: row for row in parsed}
    if len(parsed) != 39 or len(source_rows) != 39:
        raise ValueError("The foundation parser reports must cover the same 39 files once")
    for row in rows:
        source = source_rows[row["filename"]]
        if (source["source_sha256"] != row["source_sha256"]
                or source["page_count"] != row["pages"]
                or (source["status"] == "passed") != row["parser_gate_passed"]):
            raise ValueError("A file changed between its parser check and core review")
    suite = ET.parse(root / "full-unit-delivery-final.xml").getroot()
    totals = {key: sum(int(item.get(key, 0)) for item in suite.findall("testsuite"))
              for key in ("tests", "failures", "errors", "skipped")}
    version = read_json(root / "version-pair-final.json")
    book = read_json(root / "book-resume-final.json")
    backend = Path(__file__).resolve().parents[1]
    outcome = {
        "evaluation_kind": "public_bounded_official_39_acceptance_snapshot",
        "date": "2026-10-11", "new_model_calls_by_export": 0,
        "scope": "39 official PDF files, one bounded QA sample per file. Manual review of "
                 "requested core fields only; not complete answers or all six modules.",
        "cloud_budget": {"authorized": 42, "used": qa["model_calls"] + targeted["model_calls"],
                         "remaining": 0, "automatic_retries": False},
        "cloud_model": {"provider": qa["provider"], "model": qa["model"]},
        "corpus_summary": core["summary"],
        "whole_answer_acceptance": core["whole_answer_acceptance"],
        "source_review": {"reviewer": core["reviewer"], "scope": core["scope"],
                          "complete_gold_pages_viewed": 39,
                          "additional_complete_cited_pages_viewed": 5,
                          "not_independent_expert_review": True},
        "targeted_three": {"actual_calls": targeted["model_calls"],
                           "original_source_review": targeted_review["summary"],
                           "zero_call_replay_status": targeted_replay["status"],
                           "zero_call_replay_cases": len(targeted_replay["cases"]),
                           "not_a_new_generation": True},
        "saved_output_replay": {**replay["summary"], "new_model_calls": 0,
                                "new_retrieval_calls": 0, "not_a_new_generation": True},
        "version_pair": {"status": version["status"], "checks": version["checks"],
                         "not_learned_semantic_similarity": True},
        "book_engineering": {"status": book["status"], "checks": book["checks"],
                             "metrics": book["metrics"],
                             "node_counts_are_not_accuracy_scores": True},
        "backend_regression": totals,
        "index_actions": dict(Counter(row["index_action"] for row in rows)),
        "production_library_modified": False,
        "sparse_note": qa["sparse_note"],
        "runtime_browser_verified_this_run": False,
        "original_generation_code_sha256": qa["runtime_code_sha256"],
        "candidate_repaired_production_code_sha256": {
            name: digest(backend / name) for name in PRODUCTION_FILES},
        "local_artifacts_sha256": {name: digest(root / name) for name in ARTIFACTS},
        "original_questions_sha256": qa["dataset_sha256"],
        "manual_policy_sha256": core["manual_policy_sha256"],
        "files": rows,
        "known_limits": [
            "37/39 is a requested-core-field source review, not full-answer or general accuracy.",
            "The original cloud machine score remains 33/39; all failed results are retained.",
            "PPO mechanism is incomplete; Qwen was empty in the original published response.",
            "Four core-accepted answers retain language, extra content or variant scope holds.",
            "New prompt effectiveness requires separately authorized fresh generation.",
            "Parser foundation checks do not certify every formula, table cell or structural node.",
            "Python allocation peak is not total RSS, GPU memory or deployment sizing.",
            "Final original-nine generation, browser checks and six-module gates remain open.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "sha256": digest(output), "model_calls": 0,
            "core_summary": core["summary"], "backend_regression": totals}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(export(arguments.root, arguments.output), ensure_ascii=False))
