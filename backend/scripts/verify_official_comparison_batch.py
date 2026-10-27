"""One bounded four-question cross-paper batch on a fresh public BGE index.

Reuses existing unchanged independent premises/checks; records every generation
and comparison-editor request. No saved answers, production data or retries.
"""

import argparse
import json
import os
import re
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from official_index_reuse import restore_verified_index, saved_index_metadata
from official_vector_audit import AuditedQdrantClient, ReadOnlyAuditedQdrantClient
from prepare_official_browser_fixture import saved_inputs
from verify_official_local_features import seed
from verify_official_output_features import configure, select_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument(
        "--service-network", choices=["host", "compose"], default="host",
        help="Loopback ports, or existing pdfagent Compose-network DNS from an isolated container",
    )
    parser.add_argument(
        "--reuse-index-from", type=Path,
        help="Verify every payload of an isolated runtime index; fresh queries, no vector writes",
    )
    parser.add_argument(
        "--embedding-batch-size", type=int, choices=range(1, 33), default=16,
        metavar="1..32", help="Inference memory budget only; no text/model/criteria changes",
    )
    parser.add_argument(
        "--case-id",
        choices=[
            "dpo-instructgpt-object-and-stage",
            "dpo-reject-global-analytic-and-sampling-free",
            "lora-qlora-mechanism",
            "unreported-training-seed",
        ],
        help="One unchanged case only; not a full-suite pass",
    )
    args = parser.parse_args()
    if not args.allow_cloud:
        raise ValueError("Public-excerpt cloud authorization required")
    names = [
        "1706.03762v1.pdf",
        "2106.09685v2.pdf",
        "2305.14314v1.pdf",
        "2305.18290v3.pdf",
        "2203.02155v1.pdf",
    ]
    inputs = saved_inputs(args.fixture, names)
    saved_metadata = (
        saved_index_metadata(args.reuse_index_from, select_sources(args.fixture, names))
        if args.reuse_index_from else None
    )
    root = isolated_output(args.output_dir)
    configure(root)
    os.environ.update(
        VECTOR_SEARCH_ENABLED="true",
        EMBEDDING_PROVIDER="openai-compatible",
        EMBEDDING_MODEL="BAAI/bge-m3",
        EMBEDDING_DIMENSIONS="1024",
        EMBEDDING_BASE_URL="http://127.0.0.1:8001/v1",
        EMBEDDING_BATCH_SIZE=str(args.embedding_batch_size),
        QDRANT_URL="http://127.0.0.1:7333",
        VECTOR_TIMEOUT_SECONDS="15",
        VECTOR_COLLECTION="official_comparison_batch_" + uuid4().hex,
        RERANKER_PROVIDER="tei",
        RERANKER_BASE_URL="http://127.0.0.1:8002",
        NO_PROXY="localhost,127.0.0.1,::1",
    )
    if saved_metadata:
        os.environ["VECTOR_COLLECTION"] = saved_metadata["vector_collection"].split("_semantic_")[0]
    if args.service_network == "compose":
        os.environ.update(
            EMBEDDING_BASE_URL="http://bge-embedding:80/v1",
            RERANKER_BASE_URL="http://bge-reranker:80",
            QDRANT_URL="http://qdrant:6333",
            NO_PROXY="localhost,127.0.0.1,::1,bge-embedding,bge-reranker,qdrant",
        )
    from evaluate_official_all_document_qa import source_anchor_valid
    from fastapi.testclient import TestClient
    from verify_official_candidate_qa import (
        FILES,
        POLICY_FILES,
        comparison_checks,
        counterexample_checks,
        mechanism_tasks,
        policy_checks,
        policy_tasks,
    )

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.embeddings.factory import get_embedding_provider
    from app.llm.factory import get_llm_provider
    from app.main import app
    from app.schemas.agent import AskResponse
    from app.services.chunking import build_chunk_drafts, replace_document_chunks
    from app.services.vector_index import QdrantVectorIndex

    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Actual DeepSeek is required")
    settings.ensure_directories()
    indexed = seed(root, inputs)
    rows = json.loads((root / "fixture.json").read_bytes())
    by_name = {row["filename"]: row for row in rows}
    mechanism, seed_case = mechanism_tasks(root, [by_name[name] for name in FILES])
    policy = policy_tasks(root, [by_name[name] for name in POLICY_FILES])
    tasks = [("policy", task) for task in policy] + [("mechanism", task) for task in mechanism]
    if args.case_id:
        tasks = [(suite, task) for suite, task in tasks if task[0] == args.case_id]
    drafts = {doc_id: build_chunk_drafts(item["parsed"]) for doc_id, item in indexed.items()}
    report = {
        "status": "running",
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "sources": rows,
        "scope": "Four unchanged public-paper questions; actual BGE/TEI/DeepSeek ASGI",
        "index_mode": "verified_read_only_reuse" if saved_metadata else "fresh_index",
        "new_retrieval": True,
        "saved_answers_or_evidence_replayed": False,
        "service_network": args.service_network,
        "cases": [],
        "embedding_calls": [],
        "vector_operations": [],
        "index_documents": [],
        "phase": "preparing_index",
        "vector_timeout_seconds": settings.vector_timeout_seconds,
        "embedding_batch_size": settings.embedding_batch_size,
        "cloud_calls": 0,
        "production_library_modified": False,
        "automatic_retries": 0,
        "scientific_review": "pending",
    }
    calls = []

    def save():
        report["cloud_calls"] = len(calls)
        write_json(root / "report.json", report)
        write_json(root / "model-calls.json", calls)

    embedder = get_embedding_provider()
    original_embed = embedder.embed_dense

    def embed(texts):
        row = {"input_count": len(texts), "status": "started"}
        report["embedding_calls"].append(row)
        save()
        started = perf_counter()
        try:
            result = original_embed(texts)
            row.update(status="returned", dimensions=[len(vector) for vector in result])
            return result
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            row["duration_ms"] = round((perf_counter() - started) * 1000, 2)
            save()

    embedder.embed_dense = embed
    index_class = QdrantVectorIndex
    if saved_metadata:
        from evaluate_official_first_round import ReadOnlyOfficialIndex

        index_class = ReadOnlyOfficialIndex
    index = index_class(settings, embedder=embedder)
    report["temporary_vector_collection"] = index.collection_name
    audit_class = ReadOnlyAuditedQdrantClient if saved_metadata else AuditedQdrantClient
    index.client = audit_class(
        index.client, index.collection_name, report["vector_operations"], save, root,
    )
    original_post = provider._post_response

    async def post(payload):
        row = {"request": payload, "status": "started"}
        calls.append(row)
        save()
        try:
            result = await original_post(payload)
            row.update(status="returned", response=result)
            return result
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            save()

    provider._post_response = post
    try:
        with SessionLocal() as db:
            if saved_metadata:
                count = restore_verified_index(db, index, root, rows, saved_metadata)
                report["all_reused_payloads_verified"] = count
                report["reused_metadata"] = saved_metadata
            else:
                for doc_id, item in indexed.items():
                    replace_document_chunks(db, doc_id, item["parsed"])
                db.commit()
            total = 0
            for doc_id in indexed:
                entry = {"document_id": doc_id, "expected_chunks": len(drafts[doc_id]),
                         "status": "started"}
                report["index_documents"].append(entry)
                save()
                started = perf_counter()
                try:
                    count = (
                        index.document_point_count(doc_id) if saved_metadata
                        else index.index_document(db, doc_id)
                    )
                    total += count
                    entry.update(status="returned", indexed_chunks=count)
                except Exception:
                    entry["status"] = "failed"
                    raise
                finally:
                    entry["duration_ms"] = round((perf_counter() - started) * 1000, 2)
                    save()
            report["indexed_chunks"] = total
        with (
            patch("app.services.vector_index.get_vector_index", return_value=index),
            patch("app.agent.harness.get_llm_provider", return_value=provider),
            TestClient(app) as client,
        ):
            for suite, (case_id, request) in tasks:
                report["phase"] = "question:" + case_id
                before = len(calls)
                response = client.post("/api/v1/agent/ask", json=request)
                row = {
                    "suite": suite,
                    "case_id": case_id,
                    "request": request,
                    "http_status": response.status_code,
                    "cloud_calls": len(calls) - before,
                }
                report["cases"].append(row)
                response.raise_for_status()
                payload = response.json()
                row["response"] = payload
                row["checks"] = (
                    policy_checks(
                        payload, request["document_ids"], counterexample=case_id == policy[1][0]
                    )
                    if suite == "policy"
                    else comparison_checks(payload, request["document_ids"])
                    if case_id == mechanism[0][0]
                    else counterexample_checks(seed_case, payload, request["document_ids"][0])
                )
                answer = AskResponse.model_validate(payload)
                row["checks"]["all_raw_source_anchors_valid"] = bool(answer.evidence) and all(
                    e.document_id in indexed
                    and source_anchor_valid(
                        e, indexed[e.document_id]["parsed"], drafts[e.document_id]
                    )
                    for e in answer.evidence
                )
                row["status"] = (
                    "machine_passed_pending_source_review"
                    if all(row["checks"].values())
                    else "failed"
                )
                save()
                print(f"{case_id}: {row['status']}", flush=True)
        report["status"] = "complete_pending_source_review"
        report["phase"] = "finished"
    except Exception as exc:
        report.update(
            status="failed",
            error={
                "type": type(exc).__name__,
                "message": re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc))[:1000],
            },
        )
    finally:
        save()
    print(
        json.dumps(
            {
                "status": report["status"],
                "executed": len(report["cases"]),
                "cloud_calls": report["cloud_calls"],
                "error": report.get("error"),
            }
        ),
        flush=True,
    )
    return (
        0
        if len(report["cases"]) == len(tasks)
        and all(
            row.get("status") == "machine_passed_pending_source_review" for row in report["cases"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
