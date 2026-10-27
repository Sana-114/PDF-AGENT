"""Real local BGE ranking/API persistence using recorded official PDF metadata.

This is NOT a live arXiv feed or multi-user acceptance. Only a fresh isolated
database is written. Publication timestamps are unknown, not invented.
"""

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import fitz
from official_functional_inputs import isolated_output, write_json
from verify_official_output_features import configure, select_sources


async def run(args):
    root = isolated_output(args.output_dir)
    configure(root)
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.core.database import SessionLocal, init_db
    from app.embeddings.factory import get_embedding_provider
    from app.main import app
    from app.models.document import Document, DocumentStatus
    from app.models.recommendation import ArxivSubscription
    from app.schemas.discovery import PaperCandidate
    from app.services.recommendations import RecommendationService

    if (
        settings.embedding_provider != "openai-compatible"
        or settings.embedding_model != "BAAI/bge-m3"
    ):
        raise ValueError("Requires actual local BGE-M3 configuration")
    rows = select_sources(
        args.fixture, ["2010.11929v2.pdf", "2103.00020v1.pdf", "2106.09685v2.pdf"]
    )
    candidates = []
    init_db()
    with SessionLocal() as db:
        for row in rows:
            doc_id = row["document_id"]
            parsed = json.loads(
                (args.fixture / "parsed" / (doc_id + ".json")).read_text(encoding="utf-8")
            )
            identifier = row["filename"].split("v")[0]
            code_url = None
            if row["filename"] == "2106.09685v2.pdf":
                with fitz.open(args.fixture / "uploads" / (doc_id + ".pdf")) as native:
                    assert "https://github.com/microsoft/LoRA" in native[0].get_text()
                code_url = "https://github.com/microsoft/LoRA"
            candidates.append(
                PaperCandidate(
                    source="arxiv",
                    source_id=identifier,
                    arxiv_id=identifier,
                    title=parsed["title"],
                    authors=parsed["authors"],
                    abstract=parsed.get("abstract"),
                    landing_url=f"https://arxiv.org/abs/{identifier}",
                    pdf_url=f"https://arxiv.org/pdf/{identifier}",
                    code_url=code_url,
                    published_at=None,
                    importable=True,
                )
            )
            db.add(
                Document(
                    id=doc_id,
                    original_filename=row["filename"],
                    storage_key=doc_id + ".pdf",
                    title=parsed["title"],
                    authors_json=json.dumps(parsed["authors"]),
                    sha256=row["source_sha256"],
                    size_bytes=1,
                    status=DocumentStatus.READY,
                )
            )
        db.commit()

    class RecordedFeed:
        async def search_arxiv(self, **kwargs):
            return [candidate.model_copy(deep=True) for candidate in candidates]

    provider = get_embedding_provider(settings)
    actual_embed = provider.embed_dense
    embedding_calls = []

    def recorded_embed(texts):
        record = {"input_count": len(texts), "status": "started"}
        embedding_calls.append(record)
        try:
            vectors = actual_embed(texts)
            record.update(status="returned", dimensions=[len(vector) for vector in vectors])
            return vectors
        except Exception as exc:
            record.update(status="failed", error=str(exc))
            raise
        finally:
            write_json(root / "embedding-calls.json", embedding_calls)

    provider.embed_dense = recorded_embed
    service = RecommendationService(
        discovery=RecordedFeed(), config=settings.model_copy(update={"github_code_lookup_limit": 0})
    )
    report = {
        "status": "running",
        "cloud_calls": 0,
        "production_library_modified": False,
        "scope": (
            "Official metadata replay + actual BGE-M3 + single-workspace feedback, "
            "NOT live feed or multi-user isolation"
        ),
        "sources": rows,
        "unknown_publication_dates": True,
    }
    write_json(root / "report.json", report)
    checks = {}
    with patch("app.services.recommendations.get_embedding_provider", return_value=provider):
        with SessionLocal() as db:
            subscription = ArxivSubscription(
                name="Acceptance replay only",
                query="parameter efficient visual representation low rank adaptation",
                max_results=3,
                active=False,
            )
            db.add(subscription)
            db.commit()
            subscription_id = subscription.id
            first = await service.refresh(db, subscription_id)
            checks["three_records_inserted"] = first.error is None and first.discovered == 3
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/recommendations", params={"subscription_id": subscription_id}
            )
            payload = response.json()
            items = payload["items"]
            write_json(root / "first-recommendations.json", payload)
            checks["list_three"] = response.status_code == 200 and payload["total"] == 3
            checks["code_bonus_formula"] = all(
                abs(
                    item["final_score"]
                    - (
                        0.62 * item["relevance_score"]
                        + 0.28 * item["freshness_score"]
                        + (0.10 if item["code_url"] else 0)
                    )
                )
                < 0.0001
                for item in items
            )
            code_item = next(item for item in items if item["arxiv_id"] == "2106.09685")
            checks["code_reason_and_source"] = (
                code_item["code_url"] == "https://github.com/microsoft/LoRA"
                and "GitHub" in code_item["recommendation_reason"]
            )
            plain = next(item for item in items if item["id"] != code_item["id"])
            for item, feedback in ((code_item, "like"), (plain, "dislike")):
                updated = client.post(
                    f"/api/v1/recommendations/{item['id']}/feedback", json={"feedback": feedback}
                )
                checks[feedback + "_persisted"] = (
                    updated.status_code == 200 and updated.json()["feedback"] == feedback
                )
        with SessionLocal() as db:
            second = await service.refresh(db, subscription_id)
            checks["refresh_idempotent"] = (
                second.error is None and second.discovered == 0 and second.updated == 3
            )
        with TestClient(app) as client:
            final = client.get(
                "/api/v1/recommendations", params={"subscription_id": subscription_id}
            ).json()
            write_json(root / "final-recommendations.json", final)
            final_items = {item["id"]: item for item in final["items"]}
            checks["feedback_survives_refresh"] = (
                final_items[code_item["id"]]["feedback"] == "like"
                and final_items[plain["id"]]["feedback"] == "dislike"
            )
            checks["no_duplicate_ids"] = final["total"] == 3 and len(final_items) == 3
            checks["unrelated_subscription_scope"] = (
                client.get(
                    "/api/v1/recommendations", params={"subscription_id": "nonexistent"}
                ).json()["total"]
                == 0
            )
    checks["actual_bge_no_hash_fallback"] = len(embedding_calls) == 2 and all(
        item["status"] == "returned" and set(item["dimensions"]) == {1024}
        for item in embedding_calls
    )
    report.update(
        status="passed" if all(checks.values()) else "failed",
        checks=checks,
        embedding_calls=len(embedding_calls),
        active_scheduler=False,
        multi_user_acceptance=False,
    )
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
