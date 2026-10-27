"""Offline regrade of preserved official answers; never instantiate an LLM provider."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.agent import AskResponse  # noqa: E402
from app.services.answer_completeness import (  # noqa: E402
    QUANTITY,
    affirmative_model_values,
    model_mentions,
)
from app.services.evidence_fields import CONTEXT_QUERY  # noqa: E402
from app.services.grounded_text import normalise_fact  # noqa: E402
from scripts.evaluate_real_pdf_comparison import (  # noqa: E402
    _check_cross_document_fact,
    _check_fact,
    _contains_any,
)


def check_reviewed_fact(fact: dict, response, document_ids: dict[str, str]) -> bool:
    """Require the subject, value and derivation inputs in a source-qualified claim."""
    anchors = {item.evidence_id: item for item in response.evidence}
    for claim in response.claims:
        if any(not _contains_any(claim.text, group)
               for group in fact.get("claim_contains_any_groups", [])):
            continue
        subjects = {name for group in fact.get("claim_contains_any_groups", [])
                    for option in group for _, _, name in model_mentions(option)}
        fragments = " ".join(fact.get("source_text_contains_any", []))
        kind = "model_context" if CONTEXT_QUERY.search(fragments) else (
            "model_parameters" if any(QUANTITY.search(value)
                                      for value in fact["claim_contains_any"]) else None)
        if subjects and kind:
            values = affirmative_model_values(claim.text, kind)
            expected = {normalise_fact(option) for option in fact["claim_contains_any"]}
            if not any(values.get(subject, set()) & expected for subject in subjects):
                continue
        cited = [anchors[key] for key in claim.evidence_ids if key in anchors]
        cited = [anchor for anchor in cited if all(
            _contains_any(anchor.quote, [fragment])
            for fragment in fact.get("source_text_contains_all", [])
        )]
        if not _check_fact(fact, SimpleNamespace(claims=[claim], evidence=cited), document_ids):
            continue
        if derivation := fact.get("derivation"):
            if Decimal(derivation["left"]) - Decimal(derivation["right"]) != Decimal(
                derivation["result"]
            ):
                continue
        return True
    return False


def review(report_path: Path, dataset_path: Path, policy_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Offline review must not overwrite any existing report")
    report_bytes, dataset_bytes, policy_bytes = (
        path.read_bytes() for path in (report_path, dataset_path, policy_path)
    )
    source, dataset, policy = (
        json.loads(value) for value in (report_bytes, dataset_bytes, policy_bytes)
    )
    dataset_sha = hashlib.sha256(dataset_bytes).hexdigest()
    if dataset_sha != source["dataset_sha256"]:
        raise ValueError("Original dataset SHA differs from the preserved generation run")
    # Historical policies predate source binding. New inspections can bind their
    # findings to exactly one completed generation report, never another round.
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    if policy.get("original_report_sha256", report_sha) != report_sha:
        raise ValueError("Source inspection policy is bound to a different report")
    originals = {item["case_id"]: item for item in source["cases"]}
    all_cases = {item["case_id"]: item for item in dataset["cases"]}
    selected_ids = source.get("selected_case_ids", list(all_cases))
    if not selected_ids or len(set(selected_ids)) != len(selected_ids) or any(
        case_id not in all_cases for case_id in selected_ids
    ):
        raise ValueError("Invalid declared selection of original questions")
    expected = set(selected_ids)
    if len(originals) != len(source["cases"]) or set(originals) != expected:
        raise ValueError("Original results must contain each dataset case exactly once")
    inspections = policy["source_inspection"]["cases"]
    if set(inspections) != expected:
        raise ValueError("Source inspection must explicitly cover every case")
    document_ids = {item["filename"]: item["id"] for item in source["documents"]}
    results = []
    for case_id in selected_ids:
        case = all_cases[case_id]
        original = originals[case["case_id"]]
        failures = [item for item in original["failures"]
                    if not item.startswith("missing_or_wrong_source:")]
        facts = {}
        if original.get("response"):
            response = AskResponse.model_validate(original["response"])
            overrides = policy["fact_overrides"].get(case["case_id"], {})
            facts = {fact["fact_id"]: check_reviewed_fact(
                {**fact, **overrides.get(fact["fact_id"], {})}, response, document_ids
            ) for fact in case.get("facts", [])}
            facts.update({fact["fact_id"]: _check_cross_document_fact(fact, response, document_ids)
                          for fact in case.get("cross_facts", [])})
        else:
            failures.append("no_preserved_response")
        failures.extend(f"missing_or_wrong_source:{key}"
                        for key, passed in facts.items() if not passed)
        inspection = inspections[case["case_id"]]
        results.append({
            "case_id": case["case_id"],
            "original_machine_passed": not original["failures"],
            "reviewed_machine_passed": not failures,
            "facts": facts, "failures": failures,
            "source_inspection": inspection,
            "accepted": not failures and inspection["passed"],
        })
    outcome = {
        "evaluation_kind": "offline_regrade_and_source_inspection",
        "timestamp": datetime.now(UTC).isoformat(),
        "model_calls": 0, "original_model_calls": source["model_calls"],
        "original_report_sha256": report_sha,
        "original_dataset_sha256": dataset_sha,
        "review_policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "review_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "policy_id": policy["policy_id"],
        "selected_case_ids": selected_ids,
        "original_dataset_case_count": len(dataset["cases"]),
        "inspection_reviewer": policy["source_inspection"]["reviewer"],
        "inspection_scope": policy["source_inspection"]["scope"],
        "summary": {
            "total": len(results),
            "original_machine_passed": sum(item["original_machine_passed"] for item in results),
            "reviewed_machine_passed": sum(item["reviewed_machine_passed"] for item in results),
            "accepted": sum(item["accepted"] for item in results),
        },
        "cases": results,
    }
    outcome["status"] = "passed" if all(item["accepted"] for item in results) else "failed"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcome


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.report, args.dataset, args.policy, args.output)
    print(json.dumps({"status": result["status"], **result["summary"]}))
    if result["status"] == "failed":
        raise SystemExit(1)
