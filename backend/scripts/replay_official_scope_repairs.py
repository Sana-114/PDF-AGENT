"""Zero-call regression of preserved official-source failure outputs.

Only current deterministic publication/completeness checks are replayed. This
does not create new retrieval, generation, model review or scientific scores.
"""

import argparse
import json
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json


def main(args):
    for path in (args.previous_batch, args.current_batch):
        if not path.resolve().is_relative_to(TEMP_ROOT) or path.resolve() == TEMP_ROOT:
            raise ValueError("Only declared isolated official batch artifacts are accepted")
    root = isolated_output(args.output_dir)
    from app.llm.base import GeneratedClaim
    from app.llm.review_limits import filter_review_publication
    from app.schemas.agent import EvidenceAnchor
    from app.services.answer_completeness import missing_fields
    from app.services.grounded_text import audit_grounded_claims

    cases = []
    for name, file, expected_reason in (
        ("old-review-exclusivity", args.previous_batch / "review-source-fixed/review.json",
         "paper_not_limited_to_analysis"),
        ("old-temperature-task", args.previous_batch / "negative-scope-final/report.json",
         "sampling_temperature_task_mismatch"),
        ("restricted-path-direction", args.current_batch / "review/review.json",
         "restricted_attention_path_direction_reversed"),
    ):
        data = json.loads(file.read_bytes())
        if "cases" in data:
            data = data["cases"][0]["response"]
        evidence = [EvidenceAnchor.model_validate(e) for e in data["evidence"]]
        draft = [GeneratedClaim(c["text"], c["evidence_ids"]) for c in data["claims"]]
        claims, audit = audit_grounded_claims(draft, evidence)
        kept, warnings = filter_review_publication(claims, evidence)
        cases.append({"case": name, "input_sha256": digest(file),
                      "input_claims": len(draft), "published_claims": len(kept),
                      "audit": audit.model_dump(), "publication_warnings": warnings,
                      "expected_conflict_detected": expected_reason in str(audit),
                      "kept_original_indices": [next(i for i, c in enumerate(draft)
                        if c.text == kept_claim.text and c.evidence_ids == kept_claim.evidence_ids)
                        for kept_claim in kept]})
    file = args.current_batch / "comparison/report.json"
    case = json.loads(file.read_bytes())["cases"][2]
    from app.schemas.agent import AnswerClaim

    missing = missing_fields(case["request"]["question"],
        [AnswerClaim.model_validate(c) for c in case["response"]["claims"]],
        [EvidenceAnchor.model_validate(e) for e in case["response"]["evidence"]])
    report = {"status": "passed" if all(c["expected_conflict_detected"] for c in cases)
              and not any("分页优化器" in item for item in missing) else "failed",
              "cases": cases, "mechanism_missing_fields": missing,
              "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
              "cloud_calls": 0, "new_retrieval_calls": 0,
              "is_new_end_to_end_generation": False, "scientific_score_assigned": False}
    write_json(root / "report.json", report)
    print(json.dumps({"status": report["status"], "cases": len(cases), "cloud_calls": 0},
                     ensure_ascii=False))
    return int(report["status"] == "failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-batch", type=Path, required=True)
    parser.add_argument("--current-batch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    raise SystemExit(main(parser.parse_args()))
