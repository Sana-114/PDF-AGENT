"""Offline tool augmentation/field audits over saved evidence, not a new RAG run."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.base import GeneratedClaim  # noqa: E402
from app.schemas.agent import EvidenceAnchor  # noqa: E402
from app.services.answer_completeness import missing_fields  # noqa: E402
from app.services.grounded_text import checked_claims, render_checked_claims  # noqa: E402
from app.services.table_comparison import table_bleu_claims  # noqa: E402


def replay(report: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Never overwrite a report")
    original = report.read_bytes()
    source = json.loads(original)
    cases = []
    for case in source["cases"]:
        if not case.get("response") or not case.get("raw_generated"):
            continue
        evidence = [EvidenceAnchor.model_validate(item) for item in case["response"]["evidence"]]
        generated = [GeneratedClaim(**item) for item in case["raw_generated"]["claims"]]
        tool_claims = table_bleu_claims(case["question"], evidence)
        claims, rejected = checked_claims([*generated, *tool_claims], evidence)
        tool_published = bool(tool_claims) and all(
            any(claim.text == item.text for claim in claims) for item in tool_claims
        )
        missing = missing_fields(case["question"], claims, evidence,
                                 bleu_proof_published=tool_published)
        cases.append({"case_id": case["case_id"], "tool_claims_published": tool_published,
                      "missing_fields": missing, "rejected_claims": rejected,
                      "answer": render_checked_claims(claims),
                      "claims": [item.model_dump() for item in claims]})
    result = {"evaluation_kind": "offline_tool_augmentation_and_completeness_audit",
              "scope": "Saved sources, no fresh retrieval, generation or scientific acceptance",
              "model_calls": 0, "original_report_sha256": hashlib.sha256(original).hexdigest(),
              "cases": cases}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.report, args.output)
    print(json.dumps({"model_calls": 0, "cases": len(result["cases"])}))
