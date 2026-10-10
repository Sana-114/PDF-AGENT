"""Replay only publication checks on preserved generations; zero new model calls."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.base import GeneratedClaim  # noqa: E402
from app.schemas.agent import EvidenceAnchor  # noqa: E402
from app.services.grounded_text import checked_claims  # noqa: E402


def replay(report_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Never overwrite a report")
    original = report_path.read_bytes()
    source = json.loads(original)
    cases = []
    for case in source["cases"]:
        raw = case.get("raw_generated")
        if not raw or not case.get("response"):
            continue
        claims, rejected = checked_claims(
            [GeneratedClaim(**claim) for claim in raw["claims"]],
            [EvidenceAnchor.model_validate(anchor) for anchor in case["response"]["evidence"]],
        )
        cases.append({"case_id": case["case_id"], "rejected": rejected,
                      "accepted_claims": [claim.model_dump() for claim in claims]})
    result = {"evaluation_kind": "offline_numeric_guard_replay", "model_calls": 0,
              "original_report_sha256": hashlib.sha256(original).hexdigest(),
              "scope": "Publication checks only, not new answers or scientific acceptance",
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
    print(json.dumps({"model_calls": result["model_calls"], "cases": len(result["cases"])}))
