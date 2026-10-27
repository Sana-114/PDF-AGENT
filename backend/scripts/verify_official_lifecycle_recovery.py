"""New official-PDF lifecycle and translation-cache fault acceptance.

Writes/deletes only a new dedicated tmp fixture. Real routes, SQLite and local
Qdrant execute; no BGE, cloud provider, private library or production collection.
Archived translation bytes are restored between explicit fault injections.
"""

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_local_features import seed
from verify_official_output_features import configure


def lifecycle(root, inputs, foreign_keys):
    from fastapi.testclient import TestClient
    from qdrant_client import QdrantClient
    from sqlalchemy import event, func, select

    from app.core.config import settings
    from app.core.database import SessionLocal, engine
    from app.embeddings.hashing import HashEmbeddingProvider
    from app.main import app
    from app.models.chunk import DocumentChunk
    from app.models.document import Document
    from app.models.future_work import FutureWorkAssessment, FutureWorkDecision
    from app.models.paper_source import PaperSource
    from app.services.chunking import replace_document_chunks
    from app.services.storage import storage
    from app.services.vector_index import QdrantVectorIndex

    if foreign_keys:

        @event.listens_for(engine, "connect")
        def set_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    settings.ensure_directories()
    docs = seed(root, inputs)
    removed, survivor = list(docs)
    index = QdrantVectorIndex(
        settings.model_copy(update={"vector_search_enabled": True, "embedding_batch_size": 16}),
        client=QdrantClient(":memory:"),
        embedder=HashEmbeddingProvider(dimensions=32),
    )
    counts = {}
    with SessionLocal() as db:
        for doc_id, item in docs.items():
            counts[doc_id] = replace_document_chunks(db, doc_id, item["parsed"])
            db.add(
                PaperSource(
                    document_id=doc_id,
                    provider="acceptance",
                    source_id=doc_id,
                    pdf_url="https://arxiv.org/",
                    metadata_json="{}",
                )
            )
            db.add(
                FutureWorkDecision(
                    direction_id=doc_id,
                    source_document_id=doc_id,
                    source_json="{}",
                    decision="unreviewed",
                    reason="test",
                )
            )
            db.add(
                FutureWorkAssessment(
                    direction_id=doc_id,
                    source_document_id=doc_id,
                    request_json="{}",
                    result_json="{}",
                )
            )
        db.commit()
        for doc_id in docs:
            index.index_document(db, doc_id)
        other = db.get(Document, survivor)
        other.duplicate_of_id, other.duplicate_score = removed, 0.9
        db.commit()
    assets = {}
    for doc_id in docs:
        checkpoint = storage.checkpoint_dir(doc_id)
        checkpoint.mkdir()
        write_json(checkpoint / "acceptance.json", {"synthetic_checkpoint": True})
        translation = storage.translation_document_dir(doc_id) / "zh"
        translation.mkdir(parents=True)
        write_json(translation / "manifest.json", {"synthetic_cleanup_marker": True})
        assets[doc_id] = [
            storage.document_path(doc_id + ".pdf"),
            storage.parsed_path(doc_id),
            checkpoint,
            translation,
        ]
    with (
        patch("app.services.vector_index.get_vector_index", return_value=index),
        TestClient(app) as client,
    ):
        response = client.delete("/api/v1/documents/" + removed)
        checks = {
            "delete_http_204": response.status_code == 204,
            "deleted_document_get_404": client.get("/api/v1/documents/" + removed).status_code
            == 404,
            "repeat_delete_404": client.delete("/api/v1/documents/" + removed).status_code == 404,
            "survivor_api_readable": client.get("/api/v1/documents/" + survivor).status_code == 200,
            "owned_files_removed": all(not p.exists() for p in assets[removed]),
            "survivor_files_intact": all(p.exists() for p in assets[survivor]),
            "deleted_vector_points_zero": index.document_point_count(removed) == 0,
            "survivor_vector_points_intact": index.document_point_count(survivor)
            == counts[survivor],
        }
        sql = {}
        with SessionLocal() as db:
            for name, model, column in (
                ("chunks", DocumentChunk, DocumentChunk.document_id),
                ("paper_sources", PaperSource, PaperSource.document_id),
                ("future_decisions", FutureWorkDecision, FutureWorkDecision.source_document_id),
                (
                    "future_assessments",
                    FutureWorkAssessment,
                    FutureWorkAssessment.source_document_id,
                ),
            ):
                sql[name] = {
                    key: db.scalar(select(func.count()).select_from(model).where(column == key))
                    for key in docs
                }
                checks["deleted_" + name + "_zero"] = sql[name][removed] == 0
                checks["survivor_" + name + "_intact"] = sql[name][survivor] == (
                    counts[survivor] if name == "chunks" else 1
                )
            checks["survivor_duplicate_warning_cleared"] = (
                db.get(Document, survivor).duplicate_of_id is None
            )
    index.client.close()
    return {
        "checks": checks,
        "sql_counts_after_delete": sql,
        "initial_chunks": counts,
        "removed_document_id": removed,
        "survivor_document_id": survivor,
        "sqlite_foreign_keys": foreign_keys,
        "vector_backend": "real_qdrant_local_memory",
        "embedding_provider": "deterministic_hash_not_BGE",
    }


def translation(root, inputs, source, extended=False):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app
    from app.services.translation_store import translation_store

    source = source.resolve()
    if not source.is_relative_to(TEMP_ROOT) or source == TEMP_ROOT:
        raise ValueError("Use archived public-paper translations only")
    old = json.loads((source / "report.json").read_bytes())
    if old["status"] != "passed":
        raise ValueError("Archive must have successful original coverage")
    item = inputs[0]
    assert old["identity"]["source_sha256"] == item["row"]["source_sha256"]
    assert old["identity"]["ast_sha256"] == item["ast_sha256"]
    settings.ensure_directories()
    doc_id = next(iter(seed(root, inputs)))
    src = source / "translations" / doc_id / "zh"
    dst = translation_store.job_dir(doc_id, "zh")
    shutil.copytree(src, dst)
    original = {p.name: p.read_bytes() for p in src.glob("*.json")}
    page_file = "page-000001.json"
    pristine_page = json.loads(original[page_file])
    checks, faults = {}, []
    prefix = "/api/v1/documents/" + doc_id + "/translations/zh"

    def restore():
        for name, data in original.items():
            (dst / name).write_bytes(data)

    def snapshot():
        return {p.name: digest(p) for p in dst.glob("*.json")}

    with TestClient(app, raise_server_exceptions=False) as client:
        for fmt in ("markdown", "html"):
            for mode in ("bilingual", "translation"):
                result = client.get(prefix + "/export", params={"format": fmt, "mode": mode})
                checks["pristine_" + fmt + "_" + mode] = result.status_code == 200
        for fault in (
            "count_zero",
            "count_short",
            "count_bad_type",
            "wrong_manifest_document",
            "missing_page",
            "wrong_source_text",
            "wrong_bbox",
            "altered_formula",
            "wrong_page_document",
            "wrong_page_language",
            "invalid_json",
        ) + (
            (
                "count_large",
                "count_bool",
                "wrong_manifest_language",
                "missing_page_document",
                "missing_page_language",
                "wrong_page_provider",
                "wrong_page_model",
                "wrong_page_number",
            )
            if extended
            else ()
        ):
            restore()
            manifest = json.loads(original["manifest.json"])
            page = copy.deepcopy(pristine_page)
            if fault.startswith("count_"):
                manifest["page_count"] = {
                    "count_zero": 0,
                    "count_short": 1,
                    "count_bad_type": "broken",
                    "count_large": 9999,
                    "count_bool": True,
                }[fault]
                write_json(dst / "manifest.json", manifest)
            elif fault == "wrong_manifest_document":
                manifest["document_id"] = "other-document"
                write_json(dst / "manifest.json", manifest)
            elif fault == "wrong_manifest_language":
                manifest["target_language"] = "en"
                write_json(dst / "manifest.json", manifest)
            elif fault == "missing_page":
                (dst / page_file).unlink()
            elif fault == "invalid_json":
                (dst / page_file).write_text("{broken", encoding="utf-8")
            elif fault == "altered_formula":
                formula_page = next(
                    (
                        p
                        for p in item["parsed"]["pages"]
                        if any(
                            b.get("type") in {"formula", "code"}
                            and str(b.get("text") or "").strip()
                            for b in p["blocks"]
                        )
                    ),
                    None,
                )
                if formula_page is None:
                    raise ValueError("Need an actual protected block, not a synthetic substitute")
                name = f"page-{formula_page['page_number']:06d}.json"
                page = json.loads(original[name])
                ids = {
                    b["block_id"]
                    for b in formula_page["blocks"]
                    if b.get("type") in {"formula", "code"}
                }
                segment = next(s for s in page["segments"] if s["block_id"] in ids)
                segment["translation"] = "WRONG PROTECTED CONTENT"
                write_json(dst / name, page)
            else:
                if fault == "wrong_source_text":
                    page["segments"][0]["source_text"] += " CHANGED"
                elif fault == "wrong_bbox":
                    page["segments"][0]["bbox"] = [0, 0, 1, 1]
                elif fault == "wrong_page_document":
                    page["document_id"] = "other-document"
                elif fault == "wrong_page_language":
                    page["target_language"] = "en"
                elif fault == "missing_page_document":
                    page.pop("document_id")
                elif fault == "missing_page_language":
                    page.pop("target_language")
                elif fault == "wrong_page_provider":
                    page["provider"] = "other-provider"
                elif fault == "wrong_page_model":
                    page["model"] = "other-model"
                elif fault == "wrong_page_number":
                    page["page_number"] = 2
                write_json(dst / page_file, page)
            before = snapshot()
            status = client.get(prefix)
            exported = client.get(prefix + "/export", params={"format": "html"})
            cached = client.get(prefix + "/pages/1")
            status_payload = status.json() if status.status_code == 200 else None
            rejected = exported.status_code in {404, 409}
            failed_view = status.status_code == 404 or (
                status.status_code == 200 and status_payload["status"] == "failed"
            )
            unchanged = snapshot() == before
            restore()
            recovered = client.get(prefix + "/export", params={"format": "html"})
            row = {
                "fault": fault,
                "status_http": status.status_code,
                "status": status_payload,
                "export_http": exported.status_code,
                "cached_page_http": cached.status_code,
                "cache_read_unchanged": unchanged,
                "restored_export_http": recovered.status_code,
                "passed": rejected and failed_view and unchanged and recovered.status_code == 200,
            }
            faults.append(row)
            checks[fault] = row["passed"]
            write_json(root / "fault-progress.json", faults)
    restore()
    with TestClient(app, raise_server_exceptions=False) as client:
        checks["new_app_lifespan_restores_completed_status"] = (
            client.get(prefix).json()["status"] == "completed"
        )
        checks["new_app_lifespan_export_200"] = client.get(prefix + "/export").status_code == 200
    # A new native process, not just another TestClient context, opens persisted
    # cache files. Configuration stays confined to this known isolated fixture.
    if extended:
        before = snapshot()
        probe_code = (
            "import json,sys; from pathlib import Path; "
            "from verify_official_output_features import configure; configure(Path(sys.argv[1])); "
            "from app.main import app; from fastapi.testclient import TestClient; "
            "p='/api/v1/documents/'+sys.argv[2]+'/translations/zh'; "
            "c=TestClient(app); c.__enter__(); s=c.get(p); e=c.get(p+'/export'); "
            "c.__exit__(None,None,None); print(json.dumps({'status_http':s.status_code,"
            "'status':s.json().get('status'),'export_http':e.status_code}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe_code, str(root), doc_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        probe = (
            json.loads(result.stdout) if result.returncode == 0 else {"error": result.stderr[:1000]}
        )
        write_json(root / "new-process-probe.json", probe)
        checks["fresh_process_completed_status"] = probe.get("status") == "completed"
        checks["fresh_process_export_200"] = probe.get("export_http") == 200
        checks["fresh_process_does_not_modify_cache"] = snapshot() == before
    return {
        "checks": checks,
        "faults": faults,
        "archive_source": source.relative_to(TEMP_ROOT).as_posix(),
        "archive_report_sha256": digest(source / "report.json"),
        "translation_quality_reassessed": False,
        "new_translation_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["lifecycle", "translation"], required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--foreign-keys", action="store_true")
    parser.add_argument("--translation-source", type=Path)
    parser.add_argument("--extended-faults", action="store_true")
    args = parser.parse_args()
    names = ["1706.03762v1.pdf"] + (["2106.09685v2.pdf"] if args.mode == "lifecycle" else [])
    inputs = saved_inputs(args.fixture, names)
    root = isolated_output(args.output_dir)
    configure(root)
    os.environ.update(
        LLM_PROVIDER="mock",
        LLM_API_KEY="",
        EMBEDDING_PROVIDER="hash",
        VECTOR_SEARCH_ENABLED="false",
        RERANKER_PROVIDER="none",
    )
    report = {
        "mode": args.mode,
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "cloud_calls": 0,
        "BGE_calls": 0,
        "production_library_modified": False,
        "automatic_retries": 0,
    }
    try:
        report.update(
            lifecycle(root, inputs, args.foreign_keys)
            if args.mode == "lifecycle"
            else translation(root, inputs, args.translation_source, args.extended_faults)
        )
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as error:
        report.update(
            status="failed", error={"type": type(error).__name__, "message": str(error)[:1000]}
        )
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {k: report[k] for k in ("mode", "status", "checks", "error") if k in report},
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
