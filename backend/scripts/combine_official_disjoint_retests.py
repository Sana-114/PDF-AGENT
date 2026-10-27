"""Join disjoint same-code attempts, preserving failures and component identities.

This is an offline cohort ledger, not a new continuous generation run or a
scientific verdict. Pending selections are not attempts; duplicate executed
questions, source drift and mixed app fingerprints fail closed.
"""

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_official_all_document_qa import digest  # noqa: E402


def app_fingerprint(report):
    rows = sorted(
        (name.replace("\\", "/"), sha)
        for name, sha in report["runtime_code_sha256"].items()
        if name.replace("\\", "/").startswith("app/")
    )
    if not rows or any(len(sha) != 64 for _, sha in rows):
        raise ValueError("Recorded app fingerprints required")
    return hashlib.sha256("".join(f"{name}\0{sha}\n" for name, sha in rows).encode()).hexdigest()


def combine(original, original_sha, components, expected_app_sha):
    baseline = {row["filename"]: row for row in original["cases"]}
    docs = {row["filename"]: row for row in original["documents"]}
    if (
        original.get("evaluation_kind") != "official_all_39_one_sample_host_generation"
        or len(baseline) != 39
        or len(original["cases"]) != 39
        or len(docs) != 39
        or len(original["documents"]) != 39
        or set(docs) != set(baseline)
    ):
        raise ValueError("Require exactly 39 unique unchanged originals")
    attempts, sources, components_used = {}, {}, []
    for path, sha, part in components:
        if (
            part.get("evaluation_kind")
            != "official_unchanged_corpus_questions_readonly_generation_retest"
            or part.get("status")
            not in {"completed_pending_source_review", "stopped_provider_failure"}
            or part.get("production_library_modified") is not False
            or part.get("vector_index_modified") is not False
            or part.get("automatic_retries") != 0
            or part.get("provider") != "deepseek"
            or part.get("original_report_sha256") != original_sha
            or part.get("dataset_sha256") != original["dataset_sha256"]
            or part.get("source_gold_sha256") != original["source_gold_sha256"]
            or app_fingerprint(part) != expected_app_sha
        ):
            raise ValueError("Not a terminal, isolated, unchanged same-app component")
        part_docs = {row["filename"]: row for row in part["documents"]}
        if len(part_docs) != len(part["documents"]):
            raise ValueError("Duplicate component source")
        if len({row["filename"] for row in part["cases"]}) != len(part["cases"]):
            raise ValueError("Duplicate component attempt")
        if part["model_calls"] != sum(row["model_calls"] for row in part["cases"]):
            raise ValueError("Unaccounted provider attempts")
        for case in part["cases"]:
            name = case["filename"]
            if name in attempts or name not in baseline or name not in part_docs:
                raise ValueError("Executed attempts must be disjoint original questions")
            if (
                case["question"] != baseline[name]["question"]
                or case["model_calls"] != 1
                or case["status"] not in {"failed", "machine_passed_pending_source_review"}
            ):
                raise ValueError("Changed question or missing/repeated generation")
            doc = part_docs[name]
            if any(
                doc[key] != docs[name][key]
                for key in ("sha256", "ast_sha256", "id", "pages", "collection")
            ):
                raise ValueError("Source, AST or vector namespace changed")
            if case["status"] == "machine_passed_pending_source_review" and (
                case.get("response", {}).get("provider") != "deepseek"
                or case.get("raw_provider_output") is None
            ):
                raise ValueError("Fallback output cannot masquerade as generated success")
            attempts[name] = {**copy.deepcopy(case), "component_report_sha256": sha}
            sources[name] = copy.deepcopy(doc)
        components_used.append(
            {
                "path": path,
                "sha256": sha,
                "executed": len(part["cases"]),
                "calls": part["model_calls"],
            }
        )
    if set(attempts) != set(baseline):
        raise ValueError("Incomplete cohort; never pad with historical successes")
    names = list(baseline)
    passed = sum(
        row["status"] == "machine_passed_pending_source_review" for row in attempts.values()
    )
    return {
        "evaluation_kind": "offline_disjoint_same_candidate_attempt_cohort_not_new_generation_run",
        "status": "complete_attempt_coverage_pending_source_review",
        "new_cloud_calls": 0,
        "model_calls": 39,
        "automatic_retries": 0,
        "single_continuous_generation_run": False,
        "original_report_sha256": original_sha,
        "component_reports": components_used,
        "app_sha256": expected_app_sha,
        "dataset_sha256": original["dataset_sha256"],
        "source_gold_sha256": original["source_gold_sha256"],
        "production_library_modified": False,
        "vector_index_modified": False,
        "documents": [sources[name] for name in names],
        "cases": [attempts[name] for name in names],
        "scientific_source_review": "pending_independent_requested_core_review",
        "whole_answer_acceptance": "not_certified",
        "summary": {"files_attempted": 39, "machine_passed": passed, "failed": 39 - passed},
    }


def run(args):
    if args.output.exists():
        raise ValueError("Never overwrite original or previous cohort evidence")
    parts = [(str(path), digest(path), json.loads(path.read_bytes())) for path in args.reports]
    outcome = combine(
        json.loads(args.original.read_bytes()), digest(args.original), parts, args.app_sha256
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcome["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--app-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args())))
