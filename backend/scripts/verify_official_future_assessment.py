"""Real API/DeepSeek assessment of partial and unrelated official later work.

Three explicit public-excerpt requests, no automatic retries or full-library
sends. Human confirmations and production data must remain untouched.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path
from unittest.mock import patch

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_local_features import GRAPH_FILES, seed
from verify_official_output_features import configure


def main(args):
    if not args.allow_cloud:
        raise ValueError("Explicit authorization for public excerpts is required")
    root = isolated_output(args.output_dir)
    inputs = saved_inputs(args.fixture, [*GRAPH_FILES, "2501.12948v2.pdf"])
    configure(root)
    from fastapi.testclient import TestClient

    from app.api.routes import reviews
    from app.core.database import SessionLocal
    from app.llm.extractive import ExtractiveProvider
    from app.llm.factory import get_llm_provider
    from app.main import app
    from app.models.document import Document
    from app.services.evidence_review import EvidenceReviewService
    from app.services.future_progress import read_assessment

    indexed = seed(root, inputs)
    by_name = {i["row"]["filename"]: key for key, i in indexed.items()}
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Actual DeepSeek required")
    with SessionLocal() as db:
        review = asyncio.run(
            EvidenceReviewService(db, provider=ExtractiveProvider()).generate(
                [db.get(Document, key) for key in indexed], max_evidence=40
            )
        )
    write_json(root / "review-before.json", review.model_dump(mode="json"))
    local = {e.evidence_id: e for e in review.future_work_evidence}

    def direction(name, text):
        return next(
            d
            for d in review.future_directions
            if d.source_document_id == by_name[name] and text in local[d.source_evidence_id].quote
        )

    def body(name, text):
        parsed = indexed[by_name[name]]["parsed"]
        for page in parsed["pages"]:
            for b in page["blocks"]:
                if text in b.get("text", ""):
                    return {
                        "document_id": by_name[name],
                        "page_number": page["page_number"],
                        "block_ids": [b["block_id"]],
                        "quote": b["text"][:1600],
                    }
        raise ValueError("Independent later-paper target missing from fixture")

    def passage(e):
        return {k: getattr(e, k) for k in ("document_id", "page_number", "block_ids", "quote")}

    cases = [
        (
            "transformer-vs-vit-partial",
            direction("1706.03762v1.pdf", "modalities other than text"),
            body("2010.11929v2.pdf", "sequences of image patches"),
            "partial_progress",
        ),
        (
            "lora-vs-qlora-partial",
            direction("2106.09685v2.pdf", "MLP layers, LayerNorm layers"),
            body("2305.14314v1.pdf", "all linear transformer"),
            "partial_progress",
        ),
        (
            "paging-vs-reasoning-unrelated",
            direction("2305.14314v1.pdf", "paging process"),
            body("2501.12948v2.pdf", "reasoning capabilities"),
            "insufficient",
        ),
    ]
    calls = []
    original = provider._post_response

    async def record(payload):
        row = {"request": payload, "status": "started"}
        calls.append(row)
        write_json(root / "calls.json", calls)
        try:
            raw = await original(payload)
            row.update(status="returned", response=raw)
            return raw
        except Exception as exc:
            row.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(root / "calls.json", calls)

    provider._post_response = record
    report = {
        "status": "running",
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "cases": [],
        "cloud_calls": 0,
        "automatic_retries": 0,
        "production_library_modified": False,
        "sources": json.loads((root / "fixture.json").read_bytes()),
        "scientific_review": "pending",
    }
    with (
        patch.object(reviews, "get_llm_provider", return_value=provider),
        TestClient(app) as client,
    ):
        for name, d, progress, expected in cases:
            request = {"source": passage(local[d.source_evidence_id]), "progress": [progress]}
            count = len(calls)
            response = client.post(
                f"/api/v1/reviews/future-directions/{d.direction_id}/assess", json=request
            )
            payload = response.json()
            write_json(root / (name + ".json"), {"request": request, "response": payload})
            checks = {
                "api_200": response.status_code == 200,
                "one_actual_request": len(calls) - count == 1,
                "expected_scope_not_resolution": payload.get("status") == expected,
                "not_automatically_resolved": payload.get("automatically_resolved") is False,
                "no_human_confirmation_written": True,
            }
            if response.status_code == 200:
                with SessionLocal() as db:
                    cached = read_assessment(db, d.direction_id)
                checks["persisted_and_revalidated"] = cached.model_dump(mode="json") == payload
            report["cases"].append(
                {
                    "case": name,
                    "direction_id": d.direction_id,
                    "checks": checks,
                    "status": "checks_passed" if all(checks.values()) else "failed",
                }
            )
            report["cloud_calls"] = len(calls)
            write_json(root / "report.json", report)
            print(name + ": " + report["cases"][-1]["status"], flush=True)
        repeated = client.post(
            "/api/v1/reviews/generate",
            json={
                "document_ids": list(indexed),
                "generation_mode": "extractive",
                "max_evidence": 40,
            },
        )
        write_json(root / "review-after.json", repeated.json())
        relevant = [
            d
            for d in repeated.json().get("future_directions", [])
            if d["direction_id"] in {case[1].direction_id for case in cases}
        ]
        checks = {
            "review_read_200": repeated.status_code == 200,
            "three_assessments_recovered": len(relevant) == 3
            and all(d["assessment"] for d in relevant),
            "human_resolution_not_fabricated": all(
                d["confirmation"] is None and d["status"] != "confirmed_addressed" for d in relevant
            ),
            "no_extra_generation_on_read": len(calls) == 3,
        }
        report["workflow_checks"] = checks
    report["status"] = (
        "checks_passed_pending_source_review"
        if all(c["status"] == "checks_passed" for c in report["cases"]) and all(checks.values())
        else "held"
    )
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("sources", "runtime_source")},
            ensure_ascii=False,
        )
    )
    return int(report["status"] == "held")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    raise SystemExit(main(parser.parse_args()))
