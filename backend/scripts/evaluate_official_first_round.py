"""One attempt per original question, isolated official PDF indexes, no automatic retries."""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from qdrant_client import models
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.harness import ResearchAgent  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.llm.base import LLMResponseError  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.parsers.registry import get_parser  # noqa: E402
from app.schemas.agent import AskRequest  # noqa: E402
from app.services.chunking import replace_document_chunks  # noqa: E402
from app.services.vector_index import QdrantVectorIndex, get_vector_index  # noqa: E402
from scripts.evaluate_real_pdf_comparison import (  # noqa: E402
    _check_cross_document_fact,
    _check_fact,
)


class OneAttemptProvider:
    def __init__(self, delegate, *, require_full_retrieval=False):
        self.delegate = delegate
        self.name, self.model = delegate.name, delegate.model
        self.supports_translation = delegate.supports_translation
        self.calls = 0
        self.raw = None
        self.raw_provider_output = None
        self.request = None
        self.require_full_retrieval = require_full_retrieval

    async def generate_grounded_answer(self, question, evidence):
        if self.calls:
            raise LLMResponseError("First-round evaluation prohibits automatic retries.")
        if self.require_full_retrieval and (not evidence or any(
            item.recall_mode != "hybrid" or item.retrieval_mode != "reranked"
            for item in evidence
        )):
            raise LLMResponseError("Cloud generation requires real hybrid and reranked evidence.")
        self.request = {"question": question, "evidence": [
            item.model_dump(mode="json") for item in evidence
        ]}
        self.calls += 1
        try:
            result = await self.delegate.generate_grounded_answer(question, evidence)
        except LLMResponseError as exc:
            self.raw_provider_output = getattr(exc, "raw_grounded_output", None)
            raise
        self.raw_provider_output = getattr(result, "raw_grounded_output", None)
        self.raw = {"answer": result.answer, "claims": [
            {"text": item.text, "evidence_ids": item.evidence_ids} for item in result.claims
        ], "field_rejections": result.field_rejections,
            "citation_normalizations": result.citation_normalizations}
        return result


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def select_cases(dataset: dict, case_ids: list[str] | None, max_model_calls: int) -> list[dict]:
    """Select unchanged original questions before indexing or any cloud call."""
    originals = dataset["cases"]
    lookup = {case["case_id"]: case for case in originals}
    if len(lookup) != len(originals):
        raise ValueError("Original case IDs must be unique")
    if case_ids is None:
        selected = originals
    else:
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("Case selection must be nonempty and unique")
        if any(case_id not in lookup for case_id in case_ids):
            raise ValueError("Case selection contains unknown original questions")
        selected = [lookup[case_id] for case_id in case_ids]
    if not 1 <= max_model_calls <= 9 or len(selected) > max_model_calls:
        raise ValueError("Selected questions exceed the authorized one-attempt model budget")
    return selected


class ReadOnlyOfficialIndex(QdrantVectorIndex):
    def ensure_collection(self):
        if not self._collection_ready:
            if not self.client.collection_exists(self.collection_name):
                raise ValueError("Saved collection is absent; never recreate")
            self._collection_ready = True

    def ensure_documents(self, session, document_ids):
        query = select(Document.id)
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        for document_id in session.scalars(query).all():
            count = session.scalar(select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id == document_id))
            if self.document_point_count(document_id) != count:
                raise ValueError("Saved index count mismatch; no automatic backfill")
        return 0

    def index_document(self, session, document_id):
        raise ValueError("Read-only retest forbids indexing")

    def delete_document(self, document_id):
        raise ValueError("Read-only retest forbids deletion")


def validate_saved_index(previous: dict, dataset: dict, dataset_sha256: str) -> str:
    """Reuse only this evaluator's complete, public, isolated index; never repair it."""
    if (previous.get("evaluation_kind") != "official_original_questions_production_ask_harness"
            or previous.get("production_library_modified") is not False
            or previous.get("dataset_sha256") != dataset_sha256):
        raise ValueError("Saved report is not the unchanged isolated official evaluation")
    collection = previous.get("temporary_vector_collection", "")
    if not re.fullmatch(r"official_first_round_[a-f0-9]{32}", collection):
        raise ValueError("Reuse cannot access a production or arbitrary collection")
    records = previous.get("documents", [])
    expected = {entry["filename"]: entry for entry in dataset["files"]}
    if (len(records) != len(expected)
            or {d["filename"] for d in records} != set(expected)
            or len({d["id"] for d in records}) != len(records)):
        raise ValueError("Saved index must contain every declared public document exactly once")
    for item in records:
        if (item["sha256"] != expected[item["filename"]]["sha256"]
                or item["origin"] != expected[item["filename"]]["origin"]
                or item["chunks"] <= 0 or item["pages"] <= 0):
            raise ValueError("Saved public document identity or index counts differ")
    return collection


def restore_saved_documents(session, index, records) -> dict[str, str]:
    """Read scoped payloads into disposable SQLite without parsing or vector writes."""
    ids = {}
    for item in records:
        print(f"Restoring read-only index: {item['filename']}", flush=True)
        session.add(Document(id=item["id"], original_filename=item["filename"],
                             storage_key=item["filename"], size_bytes=1,
                             sha256=item["sha256"], title=item["title"],
                             page_count=item["pages"], status=DocumentStatus.READY))
        session.flush()
        offset, count, seen = None, 0, set()
        while True:
            points, offset = index.client.scroll(
                collection_name=index.collection_name,
                scroll_filter=models.Filter(must=[models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=item["id"]))]),
                limit=128, offset=offset, with_payload=True, with_vectors=False,
            )
            for point in points:
                p = point.payload
                if (not p or p["document_id"] != item["id"]
                        or str(point.id) != p["chunk_id"] or p["chunk_id"] in seen
                        or not 1 <= p["page_number"] <= item["pages"]):
                    raise ValueError("Invalid or out-of-scope saved evidence payload")
                seen.add(p["chunk_id"])
                session.add(DocumentChunk(
                    id=p["chunk_id"], document_id=item["id"], chunk_index=count,
                    page_number=p["page_number"], block_ids_json=json.dumps(p["block_ids"]),
                    bbox_json=json.dumps(p["bbox"]) if p.get("bbox") else None,
                    section=p.get("section"), text=p["text"], char_count=len(p["text"]),
                ))
                count += 1
            if offset is None:
                break
        if count != item["chunks"]:
            raise ValueError("Saved index count mismatch; no automatic backfill is allowed")
        session.commit()
        ids[item["filename"]] = item["id"]
    return ids


async def _run_once(args) -> dict:
    if not args.allow_cloud:
        raise ValueError("--allow-cloud requires explicit user authorization")
    dataset_bytes = args.dataset.read_bytes()
    dataset = json.loads(dataset_bytes)
    cases = select_cases(dataset, args.case_ids, args.max_model_calls)
    if args.output.exists():
        raise ValueError("Existing reports cannot be overwritten")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    paths = {}
    # Verify every input before any generation. No filename/path substitutions.
    for entry in dataset["files"]:
        filename = entry["filename"]
        if Path(filename).name != filename:
            raise ValueError("Input filename may not contain directories")
        root = args.supplements if entry["origin"] == "public_supplement" else args.corpus
        path = root / filename
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"SHA mismatch: {filename}")
        paths[filename] = path
    delegate = get_llm_provider()
    if delegate.name != "deepseek" or not settings.vector_search_enabled:
        raise ValueError("Real DeepSeek generation and configured vector search are required")
    previous = None
    if getattr(args, "query_report", None):
        previous_bytes = args.query_report.read_bytes()
        previous = json.loads(previous_bytes)
        collection = validate_saved_index(
            previous, dataset, hashlib.sha256(dataset_bytes).hexdigest()
        )
    else:
        collection = f"official_first_round_{uuid4().hex}"
    settings.vector_collection = collection  # Process-local; never use the user's collection.
    settings.parsed_dir = args.output.parent / "parsed"
    settings.parsed_dir.mkdir(exist_ok=True)
    report = {
        "evaluation_kind": "official_original_questions_production_ask_harness",
        "timestamp": datetime.now(UTC).isoformat(), "status": "running",
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "model": delegate.model, "provider": delegate.name,
        "max_model_calls": args.max_model_calls, "model_calls": 0,
        "selected_case_ids": [case["case_id"] for case in cases],
        "original_dataset_case_count": len(dataset["cases"]),
        "automatic_retries": False, "production_library_modified": False,
        "index_reused_read_only": bool(previous), "vector_index_modified": not bool(previous),
        "parser_calls": 0,
        "source_report_sha256": hashlib.sha256(previous_bytes).hexdigest() if previous else None,
        "runtime_code_sha256": {str(p.relative_to(Path(__file__).parents[1])):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__), *sorted((Path(__file__).parents[1] / "app").rglob("*.py"))]},
        "retrieval_settings": {k: getattr(settings, k) for k in (
            "embedding_provider", "embedding_model", "embedding_dimensions", "reranker_provider",
            "reranker_model", "rag_min_score", "reranker_candidate_k", "vector_prefetch_k")},
        "sparse_note": "BGE-M3 learned dense + hash sparse/BM25, not learned BGE sparse",
        "temporary_vector_collection": collection, "documents": [], "cases": [],
    }
    write_report(args.output, report)
    args._created_report_this_run = True
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    index = ReadOnlyOfficialIndex() if previous else get_vector_index()
    if previous and (index.collection_name != previous["temporary_vector_collection_actual"]
                     or not index.client.collection_exists(index.collection_name)):
        raise ValueError("Saved collection is absent or model namespace differs; never recreate")
    with Session(engine, expire_on_commit=False) as session, patch(
        "app.services.vector_index.get_vector_index", return_value=index
    ):
        ids = {}
        report["temporary_vector_collection_actual"] = index.collection_name
        if previous:
            ids = restore_saved_documents(session, index, previous["documents"])
            report["documents"] = previous["documents"]
            write_report(args.output, report)
        for entry in [] if previous else dataset["files"]:
            name = entry["filename"]
            print(f"Parsing and indexing {name}", flush=True)
            parsed = get_parser(str(paths[name])).parse(str(paths[name]), batch_size=25)
            report["parser_calls"] += 1
            doc = Document(
                original_filename=name, storage_key=f"official-test-{name}",
                size_bytes=paths[name].stat().st_size, sha256=entry["sha256"],
                status=DocumentStatus.READY, title=parsed.title, page_count=len(parsed.pages),
                authors_json=json.dumps(parsed.authors, ensure_ascii=False),
            )
            session.add(doc)
            session.flush()
            data = parsed.to_dict()
            (settings.parsed_dir / f"{doc.id}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            chunks = replace_document_chunks(session, doc.id, data)
            session.commit()
            indexed = index.index_document(session, doc.id)
            if indexed != chunks or indexed == 0:
                raise ValueError(f"Incomplete vector index for {name}")
            ids[name] = doc.id
            report["documents"].append({**entry, "id": doc.id, "title": parsed.title,
                                        "pages": doc.page_count, "chunks": chunks})
            write_report(args.output, report)
        for case in cases:
            if report["model_calls"] >= args.max_model_calls:
                raise ValueError("Model call budget exhausted")
            print(f"Running one attempt: {case['case_id']}", flush=True)
            provider = OneAttemptProvider(delegate, require_full_retrieval=True)
            started = perf_counter()
            result = {"case_id": case["case_id"], "question": case["question"],
                      "status": "failed", "manual_review": "pending", "failures": []}
            try:
                response = await ResearchAgent(session, provider=provider).ask(
                    AskRequest(question=case["question"],
                               document_ids=[ids[name] for name in case["documents"]], top_k=18),
                    diversify_documents=len(case["documents"]) > 1,
                )
                result["response"] = response.model_dump(mode="json")
                facts = {fact["fact_id"]: _check_fact(fact, response, ids)
                         for fact in case.get("facts", [])}
                facts.update({fact["fact_id"]: _check_cross_document_fact(fact, response, ids)
                              for fact in case.get("cross_facts", [])})
                for fact in case.get("facts", []):
                    if derivation := fact.get("derivation"):
                        facts[fact["fact_id"]] &= (
                            Decimal(derivation["left"]) - Decimal(derivation["right"])
                            == Decimal(derivation["result"])
                        )
                result["facts"] = facts
                result["failures"] += [f"missing_or_wrong_source:{key}"
                                       for key, passed in facts.items() if not passed]
                if response.provider != "deepseek" or response.insufficient_evidence:
                    result["failures"].append("no_successful_model_answer")
                if not response.evidence or any(
                    item.recall_mode != "hybrid" or item.retrieval_mode != "reranked"
                    for item in response.evidence
                ):
                    result["failures"].append("not_full_hybrid_reranked_chain")
                anchors = {item.evidence_id: item for item in response.evidence}
                if not response.claims or any(
                    not claim.evidence_ids or any(
                        key not in anchors or not anchors[key].bbox or not anchors[key].block_ids
                        for key in claim.evidence_ids
                    ) for claim in response.claims
                ):
                    result["failures"].append("missing_valid_source_anchor")
                if not result["failures"]:
                    result["status"] = "machine_passed_pending_manual_review"
            except Exception as exc:
                result["failures"].append(f"{type(exc).__name__}: {exc}")
            result["raw_generated"] = provider.raw
            result["raw_provider_output"] = provider.raw_provider_output
            result["generation_request"] = provider.request
            result["model_calls"] = provider.calls
            result["latency_seconds"] = round(perf_counter() - started, 3)
            report["model_calls"] += provider.calls
            report["cases"].append(result)
            write_report(args.output, report)  # Preserve failed responses before the next case.
            print(json.dumps({"case_id": result["case_id"], "status": result["status"],
                              "failures": result["failures"]}, ensure_ascii=False), flush=True)
    engine.dispose()
    report["status"] = (
        "failed" if any(item["failures"] for item in report["cases"])
        else "machine_passed_pending_manual_review"
    )
    write_report(args.output, report)
    return report


async def run(args) -> dict:
    """Preserve a terminal report on runtime/preflight errors, never retry."""
    args._created_report_this_run = False
    try:
        return await _run_once(args)
    except Exception as exc:
        if args._created_report_this_run and args.output.is_file():
            report = json.loads(args.output.read_text(encoding="utf-8"))
            if report.get("status") == "running":
                report["status"] = "stopped_runtime_error"
                report["error_type"] = type(exc).__name__
                report["error_phase"] = (
                    "before_generation" if report.get("model_calls", 0) == 0 else "during_batch"
                )
                write_report(args.output, report)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--supplements", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-model-calls", type=int, default=9)
    parser.add_argument("--case-ids", nargs="+", help="Unchanged original case IDs to retest")
    parser.add_argument("--query-report", type=Path,
                        help="Read-only reuse of this evaluator's saved public index; no reparse")
    parser.add_argument("--allow-cloud", action="store_true")
    arguments = parser.parse_args()
    outcome = asyncio.run(run(arguments))
    print(json.dumps({"status": outcome["status"], "model_calls": outcome["model_calls"]}))
    if outcome["status"] == "failed":
        raise SystemExit(1)
