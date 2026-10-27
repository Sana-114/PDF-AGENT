"""Provider-only regression over explicitly supplied, source-pinned public excerpts.

Run with a prepared input file; never uploads a PDF or retries generation.
Saved original failure text is checked, never rewritten or used as evidence.
"""
import argparse
import asyncio
import json
import re
from pathlib import Path

from app.llm import get_llm_provider
from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Do not overwrite a previous result")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    evidence = [EvidenceAnchor.model_validate(e) for e in data["evidence"]]
    claims = [GeneratedClaim(c["text"], c["evidence_ids"]) for c in data["cases"]]
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Real DeepSeek is required")
    calls = []
    original = provider._post_response

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def recorded(payload):
        record = {"status": "started", "request": payload}
        calls.append(record)
        save()
        try:
            response = await original(payload)
            record.update(status="returned", response=response)
            return response
        except Exception as exc:
            record.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            save()

    report = {"status": "started", "scope": "saved public excerpts; no new retrieval",
              "source_input": data, "calls": calls, "automatic_retries": 0}
    save()
    provider._post_response = recorded
    verdicts = await provider.review_claims(claims, evidence)
    report["verdicts"] = verdicts
    report["checks"] = [
        {"case": case["case_id"], "expected": case["expected"], "actual": verdict["status"],
         "passed": verdict["status"] == case["expected"]}
        for case, verdict in zip(data["cases"], verdicts, strict=True)
    ]
    report["status"] = "passed" if all(c["passed"] for c in report["checks"]) else "failed"
    save()
    print(json.dumps({"status": report["status"], "provider_calls": len(calls),
                      "checks": report["checks"]}))


if __name__ == "__main__":
    asyncio.run(main())
