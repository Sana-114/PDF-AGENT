"""Serve an isolated fixture; cloud and local BGE require separate explicit flags."""

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, write_json
from verify_official_output_features import configure


def local_service_url(value: str) -> str:
    url = urlparse(value)
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"}
            or url.username is not None or url.password is not None
            or url.port is None or url.query or url.fragment):
        raise ValueError("BGE/Qdrant acceptance may only use explicit loopback services")
    return value.rstrip("/")


async def retrieval_preflight(root, suite):
    from verify_official_candidate_qa import FILES, POLICY_FILES, mechanism_tasks, policy_tasks
    from verify_official_output_features import select_sources

    from app.agent.harness import ResearchAgent
    from app.core.database import SessionLocal
    from app.llm.base import GeneratedAnswer
    from app.schemas.agent import AskRequest

    class CaptureOnly:
        name, model = "retrieval-only-no-cloud", None

        async def generate_grounded_answer(self, question, evidence):
            return GeneratedAnswer(answer="", claims=[])

    rows = select_sources(root, POLICY_FILES if suite == "policy" else FILES)
    tasks = policy_tasks(root, rows) if suite == "policy" else mechanism_tasks(root, rows)[0]
    with SessionLocal() as db:
        result = await ResearchAgent(db, provider=CaptureOnly()).ask(AskRequest(**tasks[0][1]))
    write_json(root / "retrieval-preflight.json", {
        "kind": "actual_current_HybridRetriever_and_harness_no_LLM",
        "cloud_calls": 0, "sources": rows, "question": tasks[0][1]["question"],
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "response": result.model_dump(mode="json"),
        "all_hybrid_reranked": bool(result.evidence) and all(
            e.recall_mode == "hybrid" and e.retrieval_mode == "reranked" for e in result.evidence),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--frontend-port", type=int, default=3300)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--max-cloud-calls", type=int, default=3)
    parser.add_argument("--allow-local-bge", action="store_true")
    parser.add_argument("--embedding-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--reranker-url", default="http://127.0.0.1:8002")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:7333")
    parser.add_argument("--reuse-index-from", type=Path,
                        help="Verify and read an audited isolated index; never modify vectors")
    parser.add_argument("--preflight-suite", choices=["mechanism", "policy"],
                        help="Capture current retrieval before any cloud generation")
    args = parser.parse_args()
    root = args.fixture.resolve()
    if not root.is_relative_to(TEMP_ROOT) or root == TEMP_ROOT:
        raise ValueError("Only isolated acceptance fixtures are allowed")
    if not 8011 <= args.port <= 8099:
        raise ValueError("Only dedicated loopback acceptance ports are allowed")
    if not 3300 <= args.frontend_port <= 3399:
        raise ValueError("Only dedicated loopback frontend ports are allowed")
    if not 1 <= args.max_cloud_calls <= 10:
        raise ValueError("Choose a bounded cloud-call allowance")
    rows = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    saved_metadata = None
    if args.reuse_index_from:
        from official_index_reuse import saved_index_metadata

        if not args.allow_local_bge or args.reuse_index_from.resolve() == root:
            raise ValueError("Reuse requires local BGE and a separate fresh isolated fixture")
        saved_metadata = saved_index_metadata(args.reuse_index_from, rows)
    for row in rows:
        for directory, extension, field in [
            ("uploads", ".pdf", "source_sha256"),
            ("parsed", ".json", "ast_sha256"),
        ]:
            path = root / directory / (row["document_id"] + extension)
            if not path.resolve().is_relative_to(root) or digest(path) != row[field]:
                raise ValueError("Fixture identity changed")
    configure(root)
    os.environ["FRONTEND_ORIGIN"] = (
        "http://localhost:3300" if args.frontend_port == 3300
        else f"http://127.0.0.1:{args.frontend_port}"
    )
    if not args.allow_cloud:
        os.environ.update(LLM_PROVIDER="mock", LLM_API_KEY="")
    if args.allow_local_bge:
        os.environ.update(
            VECTOR_SEARCH_ENABLED="true", VECTOR_COLLECTION="official_runtime_" + uuid4().hex,
            EMBEDDING_PROVIDER="openai-compatible", EMBEDDING_MODEL="BAAI/bge-m3",
            EMBEDDING_DIMENSIONS="1024", EMBEDDING_BATCH_SIZE="8",
            EMBEDDING_BASE_URL=local_service_url(args.embedding_url),
            EMBEDDING_TIMEOUT_SECONDS="120", RERANKER_PROVIDER="tei",
            RERANKER_BASE_URL=local_service_url(args.reranker_url),
            QDRANT_URL=local_service_url(args.qdrant_url), VECTOR_TIMEOUT_SECONDS="30",
        )
        if saved_metadata:
            os.environ["VECTOR_COLLECTION"] = (
                saved_metadata["vector_collection"].split("_semantic_")[0]
            )
    else:
        os.environ.update(EMBEDDING_PROVIDER="hash", RERANKER_PROVIDER="none")
    import uvicorn

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.llm.factory import get_llm_provider
    from app.llm.openai_responses import OpenAIResponsesProvider
    from app.main import app
    from app.services.vector_index import QdrantVectorIndex

    metadata = {"runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
                "allow_cloud": args.allow_cloud, "allow_local_bge": args.allow_local_bge,
                "api": f"http://127.0.0.1:{args.port}", "production_library_modified": False,
                "source_fixture_sha256": digest(root / "fixture.json"),
                "max_cloud_calls": args.max_cloud_calls if args.allow_cloud else 0}
    if args.allow_cloud:
        provider = get_llm_provider()
        if provider.name != "deepseek" or provider.model != "deepseek-flash":
            raise ValueError("Public-excerpt acceptance requires configured DeepSeek Flash")
        original = OpenAIResponsesProvider._post_response
        calls = []

        async def recorded(instance, payload):
            if len(calls) >= args.max_cloud_calls:
                from app.llm.base import LLMResponseError
                raise LLMResponseError("Isolated acceptance cloud-call ceiling reached")
            record = {"request": payload, "status": "started"}
            calls.append(record)
            write_json(root / "runtime-calls.json", calls)
            try:
                response = await original(instance, payload)
                record.update(status="returned", response=response)
                return response
            except Exception as exc:
                record.update(status="failed",
                              error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
                raise
            finally:
                write_json(root / "runtime-calls.json", calls)

        OpenAIResponsesProvider._post_response = recorded
    if args.allow_local_bge:
        if saved_metadata:
            from evaluate_official_first_round import ReadOnlyOfficialIndex
            from official_index_reuse import restore_verified_index

            index = ReadOnlyOfficialIndex(settings)
            try:
                with SessionLocal() as db:
                    count = restore_verified_index(db, index, root, rows, saved_metadata)
            except Exception as exc:
                write_json(root / "server-startup-failure.json", {
                    **metadata, "cloud_calls": 0,
                    "error": re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)),
                })
                index.client.close()
                raise
            metadata.update(vector_collection=index.collection_name, indexed_chunks=count,
                            vector_reuse="full_payload_AST_verified_read_only_new_retrieval",
                            reused_server_source_sha256=digest(
                                args.reuse_index_from / "server-source.json"))
            write_json(root / "server-source.json", metadata)
            # All factory aliases still resolve this read-only class. Existing
            # namespace writes stay forbidden, even on upload/delete endpoints.
            with patch("app.services.vector_index.QdrantVectorIndex", ReadOnlyOfficialIndex), \
                    patch("app.services.vector_index.get_vector_index", return_value=index):
                if args.preflight_suite:
                    asyncio.run(retrieval_preflight(root, args.preflight_suite))
                uvicorn.run(app, host="127.0.0.1", port=args.port)
            index.client.close()
            return
        index = QdrantVectorIndex(settings)
        if index.client.collection_exists(index.collection_name):
            raise ValueError("Never overwrite an existing vector namespace")
        with SessionLocal() as db:
            count = index.ensure_documents(db, [row["document_id"] for row in rows])
        metadata.update(vector_collection=index.collection_name, indexed_chunks=count)
        index.client.close()
    write_json(root / "server-source.json", metadata)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
