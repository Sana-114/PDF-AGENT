"""Live discovery/import/feed acceptance in a new temporary ASGI workspace.

Live feeds by default; the explicit recorded-feed option is NOT live acceptance.
No production writes or LLM calls. Eager task execution checks real task code,
not an external worker/Beat deployment or multi-user mode.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from inspect_runtime_source import inspect_source
from official_functional_inputs import digest, isolated_output, write_json
from verify_official_output_features import configure, select_sources


def run(args) -> int:
    root = isolated_output(args.output_dir)
    selected = getattr(args, "only", "all")
    recorded_feed = getattr(args, "recorded_feed", None)
    if recorded_feed and selected != "recommendations":
        raise ValueError("Recorded metadata may only be used in the explicit recommendation gate")
    configure(root)
    os.environ.update(CELERY_TASK_ALWAYS_EAGER="true", GITHUB_CODE_LOOKUP_LIMIT="0")

    import fitz
    import httpx
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.api.routes.discovery import (
        get_paper_discovery_service,
        get_reference_discovery_service,
    )
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.embeddings.factory import get_embedding_provider
    from app.main import app
    from app.models.document import Document, DocumentStatus
    from app.models.paper_source import PaperSource
    from app.schemas.discovery import PaperCandidate
    from app.services.document_content import reference_links
    from app.services.paper_discovery import PaperDiscoveryService
    from app.services.recommendations import RecommendationService
    from app.services.reference_discovery import ReferenceDiscoveryService
    from app.workers import tasks
    from app.workers.celery_app import celery_app

    if (
        settings.embedding_provider != "openai-compatible"
        or settings.embedding_model != "BAAI/bge-m3"
    ):
        raise ValueError("Requires actual local BGE-M3; do not silently substitute hash embeddings")
    rows = select_sources(args.fixture, ["1706.03762v1.pdf", "2106.09685v2.pdf"])
    transformer, lora = rows
    network, downloads, refreshes, embeddings = [], [], [], []
    replay_candidates = None
    replay_identity = None
    if recorded_feed:
        project = Path(__file__).resolve().parents[2]
        relative = recorded_feed.resolve().relative_to(project).as_posix()
        if relative != (
            "backend/tmp/official-live-discovery-2026-10-17/workflow-final/feed-first.json"
        ):
            raise ValueError("Only the explicitly audited public feed snapshot is allowed")
        manifest = json.loads(
            (
                project / "docs/evidence/official-live-discovery-acceptance-2026-10-17.json"
            ).read_text(encoding="utf-8")
        )
        expected = next(row["sha256"] for row in manifest["artifacts"] if row["path"] == relative)
        if digest(recorded_feed) != expected:
            raise ValueError("Public feed changed; do not send unverified metadata")
        feed = json.loads(recorded_feed.read_text(encoding="utf-8"))["items"]
        replay_candidates = [
            PaperCandidate(
                source="arxiv",
                source_id=item["arxiv_id"] + f"v{item['arxiv_version']}",
                title=item["title"],
                authors=item["authors"],
                abstract=item["abstract"],
                arxiv_id=item["arxiv_id"],
                arxiv_version=item["arxiv_version"],
                published_at=item["published_at"],
                venue=", ".join(item["categories"]),
                landing_url=item["landing_url"],
                pdf_url=item["pdf_url"],
                code_url=item["code_url"],
                code_stars=item["code_stars"],
                importable=True,
            )
            for item in feed
        ]
        replay_identity = {"path": relative, "sha256": expected}
    report = {
        "status": "running",
        "cloud_calls": 0,
        "automatic_retries": 0,
        "production_library_modified": False,
        "external_responses_mocked": bool(recorded_feed),
        "bge_responses_mocked": False,
        "recorded_feed": replay_identity,
        "selected_cases": selected,
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "sources": rows,
        "checks": {},
        "cases": [],
        "scope": (
            "Recorded public metadata + actual local BGE + ASGI/eager task code; NOT live arXiv"
            if recorded_feed
            else "In-process real ASGI API + live providers + local BGE + eager tasks; "
            "not browser, Beat or multi-user acceptance"
        ),
    }

    def save():
        for name, value in (
            ("report", report),
            ("network-calls", network),
            ("downloads", downloads),
            ("refreshes", refreshes),
            ("embedding-calls", embeddings),
        ):
            write_json(root / (name + ".json"), value)

    class RecordedDiscovery(PaperDiscoveryService):
        last_arxiv = 0.0

        async def search_arxiv(self, **kwargs):
            if replay_candidates is not None:
                return [item.model_copy(deep=True) for item in replay_candidates]
            return await super().search_arxiv(**kwargs)

        async def _get(self, url, *, provider, params=None):
            if provider == "arxiv":
                remaining = settings.arxiv_request_delay_seconds - (
                    time.monotonic() - self.last_arxiv
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)
                self.last_arxiv = time.monotonic()
            record = {"provider": provider, "url": url, "params": params, "status": "started"}
            network.append(record)
            number = len(network)
            started = time.monotonic()
            save()
            try:
                response = await super()._get(url, provider=provider, params=params)
                record.update(status="returned", http_status=response.status_code)
                (root / f"response-{number:03d}.txt").write_text(response.text, encoding="utf-8")
                return response
            except Exception as exc:
                record.update(
                    status="failed",
                    error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)),
                    error_type=type(exc).__name__,
                    cause_type=type(exc.__cause__).__name__,
                )
                raise
            finally:
                record["elapsed_seconds"] = round(time.monotonic() - started, 4)
                save()

        async def download(self, candidate):
            staged = await super().download(candidate)
            downloads.append(
                {
                    "source_id": candidate.source_id,
                    "pdf_url": candidate.pdf_url,
                    "sha256": staged.sha256,
                    "bytes": staged.size_bytes,
                }
            )
            save()
            return staged

    class RecordedRecommendations(RecommendationService):
        async def refresh(self, db, subscription_id):
            result = await super().refresh(db, subscription_id)
            refreshes.append(asdict(result))
            save()
            return result

    discovery = RecordedDiscovery(settings)
    recommendations = RecordedRecommendations(discovery=discovery, config=settings)
    provider = get_embedding_provider(settings)
    actual_embed = provider.embed_dense

    def recorded_embed(texts):
        started = time.monotonic()
        record = {
            "input_count": len(texts),
            "status": "started",
            "events": [],
            "input_bytes": sum(len(text.encode("utf-8")) for text in texts),
            "input_sha256": hashlib.sha256(json.dumps(texts).encode()).hexdigest(),
        }
        embeddings.append(record)
        actual_client = httpx.Client

        def trace(event, info):
            item = {"event": event, "elapsed_seconds": round(time.monotonic() - started, 4)}
            if event.endswith("failed"):
                item["exception_type"] = type(info.get("exception")).__name__
            record["events"].append(item)

        def observed_client(*args, **kwargs):
            hooks = dict(kwargs.get("event_hooks") or {})

            def request_trace(request):
                request.extensions["trace"] = trace

            hooks["request"] = [*hooks.get("request", []), request_trace]
            kwargs["event_hooks"] = hooks
            return actual_client(*args, **kwargs)

        try:
            # Observe, without changing the transport/proxy/timeout policy,
            # the provider's own fresh client and every physical request.
            with patch("app.embeddings.openai_compatible.httpx.Client", observed_client):
                vectors = actual_embed(texts)
            record.update(status="returned", dimensions=[len(v) for v in vectors])
            return vectors
        except Exception as exc:
            record.update(
                status="failed",
                error=str(exc),
                error_type=type(exc).__name__,
                cause_type=type(exc.__cause__).__name__,
            )
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 4)
            save()

    provider.embed_dense = recorded_embed
    # Only instrumentation/configuration is substituted; every network call and
    # every response is real. Keep background tasks entirely inside this DB.
    celery_app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
        task_always_eager=True,
        task_eager_propagates=True,
    )
    app.dependency_overrides[get_paper_discovery_service] = lambda: discovery
    app.dependency_overrides[get_reference_discovery_service] = lambda: ReferenceDiscoveryService(
        discovery
    )

    def case(name, action):
        if selected == "recommendations" and not name.endswith("feed-feedback-and-task"):
            return
        try:
            checks = action()
            report["cases"].append(
                {
                    "case": name,
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks,
                }
            )
        except Exception as exc:
            report["cases"].append(
                {
                    "case": name,
                    "status": "failed",
                    "error": re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)),
                }
            )
        save()

    save()
    try:
        with (
            patch("app.services.recommendations.get_embedding_provider", return_value=provider),
            patch.object(tasks, "recommendation_service", recommendations),
            TestClient(app) as client,
        ):
            seed_rows = rows if selected == "recommendations" else [transformer]
            for row in seed_rows:
                for kind, suffix in (("uploads", ".pdf"), ("parsed", ".json")):
                    shutil.copyfile(
                        args.fixture / kind / (row["document_id"] + suffix),
                        root / kind / (row["document_id"] + suffix),
                    )
            parsed = json.loads(
                (root / "parsed" / (transformer["document_id"] + ".json")).read_text(
                    encoding="utf-8"
                )
            )
            with SessionLocal() as db:
                for row in seed_rows:
                    metadata = json.loads(
                        (root / "parsed" / (row["document_id"] + ".json")).read_text(
                            encoding="utf-8"
                        )
                    )
                    db.add(
                        Document(
                            id=row["document_id"],
                            original_filename=row["filename"],
                            storage_key=row["document_id"] + ".pdf",
                            title=metadata["title"],
                            sha256=row["source_sha256"],
                            status=DocumentStatus.READY,
                            authors_json=json.dumps(metadata["authors"]),
                            page_count=len(metadata["pages"]),
                            size_bytes=(root / "uploads" / (row["document_id"] + ".pdf"))
                            .stat()
                            .st_size,
                        )
                    )
                db.commit()

            def title():
                response = client.get(
                    "/api/v1/discovery/papers",
                    params={
                        "q": (
                            "An Image is Worth 16x16 Words: "
                            "Transformers for Image Recognition at Scale"
                        ),
                        "limit": 3,
                    },
                )
                payload = response.json()
                write_json(root / "title-search.json", payload)
                found = next(
                    (
                        item
                        for item in payload.get("items", [])
                        if item.get("arxiv_id") == "2010.11929"
                    ),
                    None,
                )
                return {
                    "api_200": response.status_code == 200,
                    "correct_identity": bool(found),
                    "title_and_importable": bool(
                        found
                        and "An Image is Worth 16x16 Words" in found["title"]
                        and found["importable"]
                    ),
                }

            case("live-title-search", title)

            def imported():
                response = client.post(
                    "/api/v1/discovery/import",
                    json={"source": "arxiv", "source_id": "2106.09685v2"},
                )
                payload = response.json()
                write_json(root / "import.json", payload)
                doc = payload.get("document", {})
                checks = {
                    "api_202": response.status_code == 202,
                    "parsed_ready": doc.get("status") == "ready",
                    "exact_official_bytes": doc.get("sha256") == lora["source_sha256"],
                    "version_two": doc.get("arxiv_version") == 2
                    and doc.get("arxiv_id") == "2106.09685",
                }
                if not doc.get("id"):
                    return checks
                with SessionLocal() as db:
                    document = db.get(Document, doc["id"])
                    source = db.get(PaperSource, doc["id"])
                    source_path = root / "uploads" / document.storage_key
                    checks["persisted_source_and_file"] = bool(
                        source
                        and source.source_id == "2106.09685v2"
                        and digest(source_path) == lora["source_sha256"]
                    )
                    with fitz.open(source_path) as native:
                        checks["native_title_and_pages"] = (
                            "Low-Rank Adaptation" in native[0].get_text()
                            and len(native) == document.page_count
                        )
                        native[0].get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(
                            str(root / "lora-first-page.png")
                        )
                ast = json.loads(
                    (root / "parsed" / (doc["id"] + ".json")).read_text(encoding="utf-8")
                )
                checks["actual_parser_complete_pages"] = (
                    len(ast["pages"]) == doc["page_count"] and doc["page_count"] > 0
                )
                time.sleep(settings.arxiv_request_delay_seconds)
                duplicate = client.post(
                    "/api/v1/discovery/import",
                    json={"source": "arxiv", "source_id": "2106.09685v2"},
                )
                write_json(root / "duplicate-import.json", duplicate.json())
                checks["repeat_same_document"] = (
                    duplicate.status_code == 202
                    and duplicate.json().get("exact_duplicate") is True
                    and duplicate.json()["document"]["id"] == doc["id"]
                )
                with SessionLocal() as db:
                    checks["no_extra_library_row"] = len(db.scalars(select(Document)).all()) == 2
                return checks

            case("live-exact-import-and-duplicate", imported)

            def references():
                refs, _ = reference_links(parsed)
                reference = next(ref for ref in refs if "Adam:" in ref["text"])
                response = client.post(
                    "/api/v1/discovery/references/resolve",
                    json={
                        "document_id": transformer["document_id"],
                        "reference_ids": [reference["reference_id"]],
                        "limit": 1,
                        "candidates_per_reference": 3,
                        "selection_mode": "all",
                    },
                )
                write_json(root / "reference-input.json", reference)
                write_json(root / "reference-resolution.json", response.json())
                items = response.json().get("items", [])
                return {
                    "api_200": response.status_code == 200,
                    "one_attempt": len(items) == 1,
                    "matched_correct_paper": bool(
                        items
                        and items[0]["status"] == "matched"
                        and items[0]["candidates"]
                        and items[0]["candidates"][0]["paper"].get("arxiv_id") == "1412.6980"
                        and items[0]["candidates"][0]["paper"].get("importable")
                    ),
                    "citation_context_preserved": bool(
                        items and items[0].get("citation_value", {}).get("contexts")
                    ),
                }

            case("live-reference-drilldown", references)

            def feeds():
                subscription = client.post(
                    "/api/v1/recommendations/subscriptions",
                    json={
                        "name": "Official live feed acceptance",
                        "query": "low-rank adaptation",
                        "max_results": 3,
                        "active": True,
                        "refresh_now": False,
                    },
                )
                payload = subscription.json()
                write_json(root / "subscription.json", payload)
                if subscription.status_code != 201:
                    return {"create_201": False}
                identifier = payload["id"]
                client.post(f"/api/v1/recommendations/subscriptions/{identifier}/refresh")
                first = client.get(
                    "/api/v1/recommendations", params={"subscription_id": identifier}
                ).json()
                write_json(root / "feed-first.json", first)
                checks = {
                    "create_201": True,
                    "feed_records_added" if recorded_feed else "live_records_added": bool(
                        first["items"] and refreshes[-1]["error"] is None
                    ),
                }
                if len(first["items"]) < 2:
                    return checks
                feedback = {first["items"][0]["id"]: "like", first["items"][1]["id"]: "dislike"}
                for item_id, value in feedback.items():
                    updated = client.post(
                        f"/api/v1/recommendations/{item_id}/feedback", json={"feedback": value}
                    )
                    checks[value + "_saved"] = (
                        updated.status_code == 200 and updated.json().get("feedback") == value
                    )
                client.post(f"/api/v1/recommendations/subscriptions/{identifier}/refresh")
                second = client.get(
                    "/api/v1/recommendations", params={"subscription_id": identifier}
                ).json()
                write_json(root / "feed-second.json", second)
                second_items = {item["id"]: item for item in second["items"]}
                checks["feedback_retained"] = all(
                    item_id in second_items and second_items[item_id]["feedback"] == value
                    for item_id, value in feedback.items()
                )
                checks["refresh_without_duplicate"] = (
                    refreshes[-1]["error"] is None
                    and refreshes[-1]["discovered"] == 0
                    and refreshes[-1]["updated"] == len(first["items"])
                    and second["total"] == first["total"]
                )
                checks["scores_and_reasons"] = all(
                    item["recommendation_reason"]
                    and abs(
                        item["final_score"]
                        - (
                            0.62 * item["relevance_score"]
                            + 0.28 * item["freshness_score"]
                            + (0.10 if item["code_url"] else 0)
                        )
                    )
                    < 0.0002
                    for item in second["items"]
                )
                checks["ranking_provenance_visible"] = all(
                    item.get("ranking_provider") in {"openai-compatible", "hash"}
                    and (item["ranking_provider"] != "hash" or item.get("ranking_warning"))
                    for item in second["items"]
                )
                inactive = client.post(
                    "/api/v1/recommendations/subscriptions",
                    json={
                        "name": "Inactive must not run",
                        "query": "inactive acceptance",
                        "max_results": 1,
                        "active": False,
                        "refresh_now": False,
                    },
                )
                task_result = tasks.refresh_all_arxiv_subscriptions.apply().get()
                write_json(root / "scheduled-task-code.json", task_result)
                checks["task_active_filter"] = (
                    inactive.status_code == 201
                    and task_result["refreshed"] == 1
                    and task_result["results"][0]["subscription_id"] == identifier
                    and task_result["results"][0]["error"] is None
                )
                checks["beat_configuration"] = (
                    celery_app.conf.beat_schedule["refresh-active-arxiv-subscriptions"]["task"]
                    == "recommendations.refresh_all"
                )
                checks["actual_bge_no_fallback"] = len(embeddings) == 3 and all(
                    item["status"] == "returned" and set(item["dimensions"]) == {1024}
                    for item in embeddings
                )
                checks["summary_reports_actual_bge"] = len(refreshes) == 3 and all(
                    item.get("ranking_provider") == "openai-compatible"
                    and item.get("ranking_warning") is None
                    for item in refreshes
                )
                return checks

            case(
                "recorded-feed-feedback-and-task"
                if recorded_feed
                else "live-feed-feedback-and-task",
                feeds,
            )
    finally:
        app.dependency_overrides.clear()
        report["checks"]["official_source_and_ast_unchanged"] = all(
            digest(args.fixture / kind / (row["document_id"] + suffix)) == row[field]
            for row in rows
            for kind, suffix, field in (
                ("uploads", ".pdf", "source_sha256"),
                ("parsed", ".json", "ast_sha256"),
            )
        )
        report["status"] = (
            "passed_scoped_checks"
            if report["cases"]
            and all(item["status"] == "passed" for item in report["cases"])
            and all(report["checks"].values())
            else "partial_acceptance"
        )
        save()
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": report["cases"],
                "embedding_calls": len(embeddings),
                "metadata_calls": len(network),
                "downloads": len(downloads),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed_scoped_checks" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--only",
        choices=["all", "recommendations"],
        default="all",
        help="Use audited saved papers for a feed-only gate; no PDF downloads",
    )
    parser.add_argument(
        "--recorded-feed",
        type=Path,
        help="Explicit pinned public metadata replay; never claim live arXiv",
    )
    raise SystemExit(run(parser.parse_args()))
