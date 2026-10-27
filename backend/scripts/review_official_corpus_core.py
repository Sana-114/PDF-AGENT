"""Apply an explicit, report-bound manual core-field review without changing gold.

This tool verifies identities, cited IDs, inspected physical pages and original
retrieval coverage. The reviewer verdict is not an automatic entailment score.
"""

import argparse
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_official_all_document_qa import digest, normalized  # noqa: E402


def validate_policy(report, policy, report_sha):
    if policy.get("original_report_sha256") != report_sha:
        raise ValueError("Review policy belongs to a different immutable generation run")
    if policy.get("whole_answer_reviewed") is not False:
        raise ValueError("This bounded review cannot certify incidental claims or whole answers")
    names = [case["filename"] for case in report["cases"]]
    if len(names) != 39 or len(set(names)) != 39 or set(policy["cases"]) != set(names):
        raise ValueError("Manual verdicts must cover exactly the 39 original file samples")
    for row in policy["cases"].values():
        if (type(row.get("accepted")) is not bool or not row.get("note")
                or not isinstance(row.get("proofs"), list)
                or row["accepted"] and not row["proofs"]):
            raise ValueError("Explicit core verdicts need notes and positive citation proofs")


def review(report_path, gold_path, policy_path, corpus, output):
    if output.exists():
        raise ValueError("Do not overwrite a previous cloud or manual-review report")
    report, gold, policy = (json.loads(path.read_bytes())
                            for path in (report_path, gold_path, policy_path))
    validate_policy(report, policy, digest(report_path))
    if digest(gold_path) != report["source_gold_sha256"]:
        raise ValueError("Independent gold identity changed after generation")
    independent = {case["filename"]: case for case in gold}
    documents = {doc["filename"]: doc for doc in report["documents"]}
    if len(independent) != 39 or len(documents) != 39:
        raise ValueError("Unambiguous source identity is required for every file sample")
    rows = []
    for case in report["cases"]:
        name = case["filename"]
        doc, expected, verdict = documents[name], independent[name], policy["cases"][name]
        source = (corpus / name).resolve()
        if (Path(name).name != name or source.parent != corpus.resolve()
                or digest(source) != doc["sha256"] or doc["sha256"] != expected["sha256"]):
            raise ValueError("Public PDF identity differs from the unchanged gold or index")
        evidence = case["generation_request"]["evidence"]
        anchors = {item["evidence_id"]: item for item in case["response"]["evidence"]}
        source_found = any(item["page_number"] == expected["page"] and normalized(
            expected["quote"]) in normalized(item["quote"]) for item in evidence)
        proofs = []
        with fitz.open(source) as native:
            if len(native) != doc["pages"]:
                raise ValueError("Physical source page count changed")
            for index, key, page in verdict["proofs"]:
                claims = case["response"]["claims"]
                if (type(index) is not int or not 0 <= index < len(claims)
                        or key not in claims[index]["evidence_ids"] or key not in anchors
                        or anchors[key]["document_id"] != doc["id"]
                        or anchors[key]["page_number"] != page or not 1 <= page <= len(native)):
                    raise ValueError("A manual proof is not a cited original physical source")
                proofs.append({"claim_index": index, "evidence_id": key, "page": page})
        rows.append({"filename": name, "source_sha256": doc["sha256"], "pages": doc["pages"],
                     "parser_gate_passed": doc["parser_gate_passed"],
                     "index_action": doc["index_action"],
                     "independent_source_in_original_request": source_found,
                     "original_machine_status": case["status"],
                     "original_machine_failures": case["failures"],
                     "requested_core_accepted": verdict["accepted"],
                     "core_source_proofs": proofs, "review_note": verdict["note"],
                     "quality_holds": verdict.get("quality_holds", []),
                     "whole_answer_reviewed": False})
    outcome = {"evaluation_kind": "offline_manual_requested_core_review_not_full_answers",
               "model_calls": 0, "original_model_calls": report["model_calls"],
               "original_report_sha256": digest(report_path),
               "original_source_gold_sha256": digest(gold_path),
               "manual_policy_sha256": digest(policy_path), "reviewer": policy["reviewer"],
               "scope": policy["scope"], "whole_answer_acceptance": "not_certified",
               "summary": {"files": len(rows), "pages": sum(row["pages"] for row in rows),
                           "foundation_passed": sum(row["parser_gate_passed"] for row in rows),
                           "independent_source_retrieved": sum(row[
                               "independent_source_in_original_request"] for row in rows),
                           "original_machine_passed": sum(row["original_machine_status"] ==
                               "machine_passed_pending_source_review" for row in rows),
                           "requested_core_accepted": sum(row["requested_core_accepted"]
                               for row in rows),
                           "additional_quality_holds": sum(bool(row["quality_holds"])
                               for row in rows)}, "files": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcome["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("report", "source-gold", "policy", "corpus", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(review(args.report, args.source_gold, args.policy, args.corpus, args.output)))
