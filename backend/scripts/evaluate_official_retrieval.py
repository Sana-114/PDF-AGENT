"""Validate local BGE retrieval with independent native-source gold and a fresh index.

No cloud generation, production database, or production collection access.
Author/method probes cover each explicitly selected PDF; four reference probes
target repaired entry styles. A pass is NOT generated-answer or full PDF accuracy.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import fitz
import httpx
from qdrant_client import models
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

# Defense in depth: never load a cloud key/provider from the user's .env.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.rerankers import get_reranker  # noqa: E402
from app.services.chunking import build_chunk_drafts, replace_document_chunks  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from app.services.vector_index import QdrantVectorIndex  # noqa: E402
from scripts.evaluate_official_batch import file_hash, load_selection  # noqa: E402


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    return " ".join(value.casefold().split())


def source_probe(document, page_number: int, quote: str) -> dict:
    if not 1 <= page_number <= len(document) or not quote.strip():
        raise ValueError("Invalid source probe")
    # Native text, independent of both production AST and chunking/retrieval.
    candidates = [b for b in document[page_number - 1].get_text("blocks")
                  if len(b) > 6 and b[6] == 0 and normalized(quote) in normalized(b[4])]
    if not candidates:
        raise ValueError(f"Gold quote absent from native PDF page {page_number}: {quote}")
    box = min(candidates, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))[:4]
    return {"page_number": page_number, "quote": quote,
            "native_block_bbox": [round(float(v), 2) for v in box]}


def prepare_inputs(corpus: Path, manifest: Path, probes: Path, parse_report: Path,
                   filenames: list[str] | None = None, override_report: Path | None = None):
    entries = load_selection(corpus, manifest, filenames)
    report = json.loads(parse_report.read_text(encoding="utf-8"))
    dataset = json.loads(probes.read_text(encoding="utf-8"))
    names = {e["filename"] for e in entries}
    methods = [p for p in dataset["probes"] if p["filename"] in names]
    if len(methods) != len(names) or {p["filename"] for p in methods} != names:
        raise ValueError("Exactly one independent method probe is required per selected PDF")
    references = [p for p in dataset.get("reference_probes", []) if p["filename"] in names]
    if len(references) != len({p["filename"] for p in references}):
        raise ValueError("Reference probes must have unique filenames")
    if file_hash(manifest) != report["manifest_sha256"]:
        raise ValueError("Parser report manifest mismatch")
    parsed_entries = {e["filename"]: e for e in report["documents"]}
    if (len(parsed_entries) != len(report["documents"])
            or not names.issubset(parsed_entries)):
        raise ValueError("Parser report does not cover the selected documents uniquely")
    overrides = {}
    if override_report:
        correction = json.loads(override_report.read_text(encoding="utf-8"))
        overrides = {item["filename"]: item for item in correction["documents"]}
        if (not overrides or len(overrides) != len(correction["documents"])
                or set(overrides) - names):
            raise ValueError("AST correction report must contain unique, selected filenames")
    documents, cases = [], []
    for entry in entries:
        name = entry["filename"]
        record = overrides.get(name, parsed_entries[name])
        artifact_report = override_report if name in overrides else parse_report
        if record["status"] != "passed" or record["source_sha256"] != entry["sha256"]:
            raise ValueError(f"Basic parser checks not passed: {name}")
        job = record["artifacts_directory"]
        if Path(job).name != job:
            raise ValueError("Unsafe parser artifact path")
        ast_path = (artifact_report.parent / job / "artifacts/parsed.json").resolve()
        if not ast_path.is_relative_to(artifact_report.parent.resolve()):
            raise ValueError("AST resolves outside the parser report directory")
        ast = json.loads(ast_path.read_text(encoding="utf-8"))
        if len(ast["pages"]) != record["page_count"]:
            raise ValueError("Incomplete AST page coverage")
        documents.append({"filename": name, "sha256": entry["sha256"],
                          "ast_sha256": file_hash(ast_path), "ast": ast})
        with fitz.open(corpus / name) as native:
            author = entry["expect"]["required_authors"][0]
            cases.append({"case_id": f"authors:{name}", "filename": name, "kind": "author",
                          "question": "论文的作者或署名团队是谁？请给出首页署名的位置。",
                          "gold": source_probe(native, 1, author)})
            method = next(p for p in methods if p["filename"] == name)
            cases.append({"case_id": f"method:{name}", "filename": name, "kind": "method",
                          "question": method["question"],
                          "gold": source_probe(native, method["page"], method["quote"])})
            reference = next((p for p in references if p["filename"] == name), None)
            if reference:
                cases.append({"case_id": f"reference:{name}", "filename": name,
                              "kind": "reference",
                              "question": reference["question"],
                              "gold": source_probe(native, reference["page"],
                                                   reference["quote"])})
    return documents, cases, report["code_sha256"]


def local_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Retrieval evaluation is restricted to local model and index endpoints")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("Local URLs may not contain credentials or query data")
    return value.rstrip("/")


def anchor_valid(item, document_id: str, ast: dict) -> bool:
    if item.document_id != document_id or not 1 <= item.page_number <= len(ast["pages"]):
        return False
    page = ast["pages"][item.page_number - 1]
    box = item.bbox
    if not box or len(box) != 4 or not all(math.isfinite(v) for v in box):
        return False
    if not (0 <= box[0] < box[2] <= page["width"] + 1
            and 0 <= box[1] < box[3] <= page["height"] + 1):
        return False
    sources = [(b["block_id"], b.get("bbox")) for b in page["blocks"]]
    # Structured table/figure nodes are genuine AST anchors even without a
    # caption block. Never accept a fabricated pN-table-N merely by its pattern.
    for collection, field in [("tables", "table_id"), ("figures", "figure_id"),
                              ("formulas", "formula_id"), ("references", "reference_id")]:
        for node in ast.get(collection, []):
            if node.get("page_number") == item.page_number:
                sources.append((node[field], node.get("bbox")))
            if collection == "tables":
                for n, segment in enumerate(node.get("segments", []), 1):
                    if segment.get("page_number") == item.page_number:
                        sources.extend([(node[field], segment.get("bbox")),
                                        (f"{node[field]}-segment-{n}", segment.get("bbox"))])
    return bool(item.block_ids and any(
        node_id in item.block_ids and source_box and len(source_box) == 4
        and min(box[2], source_box[2]) > max(box[0], source_box[0])
        and min(box[3], source_box[3]) > max(box[1], source_box[1])
        for node_id, source_box in sources
    ))


def probe_hit(item, gold: dict) -> bool:
    return (item.page_number == gold["page_number"]
            and normalized(gold["quote"]) in normalized(item.quote))


class RecordingReranker:
    def __init__(self, delegate):
        self.delegate = delegate
        self.candidates = []
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def rerank(self, question, evidence, top_k):
        self.candidates = list(evidence)
        self.calls += 1
        return self.delegate.rerank(question, evidence, top_k)


def isolated_collection_base(previous: dict, *, query_only: bool) -> str:
    if previous.get("model_calls") != 0 or previous.get("production_library_modified"):
        raise ValueError("Reuse is restricted to cloud-free isolated retrieval reports")
    if query_only:
        if previous.get("status") != "completed" or not previous.get("documents"):
            raise ValueError("Query-only reuse requires a completed retrieval report")
    elif previous.get("cases"):
        raise ValueError("Only an index-stage checkpoint with no executed cases may resume")
    suffix = "_semantic_" + hashlib.sha256(b"BAAI/bge-m3").hexdigest()[:10] + "_1024"
    collection = previous.get("temporary_vector_collection", "")
    base = collection.removesuffix(suffix)
    if not collection.endswith(suffix) or not re.fullmatch(
        r"official_remaining_bge_[a-f0-9]{32}", base
    ):
        raise ValueError("Reuse may only access this evaluator's isolated collection")
    records = previous.get("documents", [])
    if len({d["filename"] for d in records}) != len(records):
        raise ValueError("Duplicate saved document records")
    return base


def chunk_signature(page, section, text, block_ids, bbox):
    return (page, section, text, tuple(block_ids), tuple(bbox or []))


def verify_restored_chunks(points, drafts) -> None:
    expected = Counter(chunk_signature(d.page_number, d.section, d.text, d.block_ids, d.bbox)
                       for d in drafts)
    actual = Counter(chunk_signature(p.payload["page_number"], p.payload["section"],
                                    p.payload["text"], p.payload["block_ids"], p.payload["bbox"])
                     for p in points)
    if expected != actual or any(str(p.id) != p.payload.get("chunk_id") for p in points):
        raise ValueError("Saved vector payloads differ from current verified chunk drafts")


class ReadOnlyEvaluationIndex(QdrantVectorIndex):
    """Fail rather than let normal automatic backfill alter prior test evidence."""

    def ensure_collection(self):
        if not self._collection_ready:
            if not self.client.collection_exists(self.collection_name):
                raise ValueError("Read-only evaluation collection is missing")
            self._collection_ready = True

    def ensure_documents(self, session, document_ids):
        query = select(Document.id)
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        for document_id in session.scalars(query).all():
            expected = session.scalar(select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id == document_id))
            if self.document_point_count(document_id) != expected:
                raise ValueError("Read-only evaluation refuses automatic vector backfill")
        return 0

    def index_document(self, session, document_id):
        raise ValueError("Read-only evaluation forbids indexing")

    def delete_document(self, document_id):
        raise ValueError("Read-only evaluation forbids deletion")


class RecordingEmbedder:
    def __init__(self, delegate):
        self.delegate, self.calls = delegate, 0

    def __getattr__(self, key):
        return getattr(self.delegate, key)

    def embed_dense(self, texts):
        self.calls += 1
        return self.delegate.embed_dense(texts)


def snapshot(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(args):
    if args.output_dir.exists():
        raise ValueError("Never overwrite previous retrieval evidence")
    documents, cases, parser_hashes = prepare_inputs(
        args.corpus, args.manifest, args.probes, args.parse_report, args.filenames,
        args.override_report,
    )
    if args.case_ids is not None:
        available = {case["case_id"]: case for case in cases}
        if (not args.case_ids or len(args.case_ids) != len(set(args.case_ids))
                or set(args.case_ids) - set(available)):
            raise ValueError("Empty, duplicate, or unknown explicit retrieval case selection")
        cases = [available[key] for key in args.case_ids]
    args.output_dir.mkdir(parents=True)
    gold_path = args.output_dir / "source-gold.json"
    gold_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.prepare_only:
        return {"documents": len(documents), "cases": len(cases), "model_calls": 0,
                "embedding_calls": 0, "status": "source_gold_prepared_only"}
    embedding_url, reranker_url, index_url = map(local_url, [
        args.embedding_url, args.reranker_url, args.qdrant_url,
    ])
    # Process-only local proxy exception. Never modify .env, Git, or global proxy settings.
    os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY", ""),
                                                "localhost,127.0.0.1,::1"]))
    settings.llm_provider, settings.llm_api_key = "mock", ""
    settings.vector_search_enabled = True
    previous = None
    previous_path = args.query_report or args.resume_report
    if previous_path:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        settings.vector_collection = isolated_collection_base(
            previous, query_only=bool(args.query_report),
        )
    else:
        settings.vector_collection = f"official_remaining_bge_{uuid4().hex}"
    settings.qdrant_url = index_url
    settings.embedding_provider = "openai-compatible"
    settings.embedding_model, settings.embedding_dimensions = "BAAI/bge-m3", 1024
    settings.embedding_base_url, settings.embedding_api_key = embedding_url + "/v1", ""
    settings.embedding_batch_size = 4
    settings.embedding_timeout_seconds = 120
    settings.vector_timeout_seconds = 30
    settings.reranker_provider, settings.reranker_model = "tei", "BAAI/bge-reranker-v2-m3"
    settings.reranker_base_url, settings.reranker_api_key = reranker_url, ""
    settings.reranker_timeout_seconds = 120
    settings.parsed_dir = args.output_dir / "parsed"
    settings.parsed_dir.mkdir()
    with httpx.Client(trust_env=False, timeout=15) as client:
        infos = {}
        for kind, base, expected in [("embedding", embedding_url, settings.embedding_model),
                                     ("reranker", reranker_url, settings.reranker_model)]:
            client.get(base + "/health").raise_for_status()
            response = client.get(base + "/info")
            response.raise_for_status()
            infos[kind] = response.json()
            if infos[kind].get("model_id") != expected:
                raise ValueError("Local service model differs from the required real BGE model")
        client.get(index_url + "/healthz").raise_for_status()
    embedding_client = httpx.Client(trust_env=False, timeout=settings.embedding_timeout_seconds)
    embedder = RecordingEmbedder(OpenAICompatibleEmbeddingProvider(
        model=settings.embedding_model, dimensions=settings.embedding_dimensions,
        base_url=settings.embedding_base_url, client=embedding_client,
    ))
    index_class = ReadOnlyEvaluationIndex if args.query_report else QdrantVectorIndex
    index = index_class(embedder=embedder)
    exists = index.client.collection_exists(index.collection_name)
    if not previous and exists:
        raise ValueError("Fresh isolated collection unexpectedly already exists")
    if previous and (not exists
                     or index.collection_name != previous["temporary_vector_collection"]):
        raise ValueError("Resume collection is missing; never recreate it")
    if previous:
        index._collection_ready = True
    report = {"evaluation_kind": "official_local_bge_retrieval_not_generation",
              "timestamp": datetime.now(UTC).isoformat(), "status": "running",
              "scope": "Explicitly selected single-document native-source probes; "
                       "no cross-document conclusion evaluation",
              "selected_filenames": [d["filename"] for d in documents],
              "selected_case_ids": [case["case_id"] for case in cases],
              "model_calls": 0, "parser_calls": 0, "automatic_retries": 0,
              "generative_llm_calls": 0,
              "index_reused_read_only": bool(args.query_report),
              "vector_index_modified": not bool(args.query_report),
              "production_library_modified": False,
              "temporary_vector_collection": index.collection_name,
              "service_info": infos, "top_k": args.top_k,
              "retrieval_settings": {
                  "rag_min_score": settings.rag_min_score,
                  "reranker_candidate_k": settings.reranker_candidate_k,
                  "reranker_retrieval_weight": settings.reranker_retrieval_weight,
                  "vector_prefetch_k": settings.vector_prefetch_k,
              },
              "sparse_note": "BGE-M3 learned dense vectors + hash sparse/BM25; "
                             "not learned BGE sparse",
              "inputs_sha256": {"manifest": file_hash(args.manifest),
                                "probes": file_hash(args.probes),
                                "source_gold": file_hash(gold_path),
                                "parse_report": file_hash(args.parse_report)},
              "parser_snapshot_code_sha256": parser_hashes,
              "retrieval_code_sha256": {str(p.relative_to(Path(__file__).parents[1])): file_hash(p)
                 for p in [Path(__file__), *sorted(
                     (Path(__file__).parents[1] / "app").rglob("*.py"))]},
              "documents": [], "cases": []}
    if args.override_report:
        report["inputs_sha256"]["override_report"] = file_hash(args.override_report)
    if previous:
        report["resumed_index_report_sha256"] = file_hash(previous_path)
        report["resumed_documents"] = []
        report["retained_superseded_vectors"] = []
    path = args.output_dir / "report.json"
    snapshot(path, report)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            ids, asts = {}, {}
            for item in documents:
                name, ast = item["filename"], item["ast"]
                action = "Restoring read-only payloads" if args.query_report else "Indexing"
                print(f"{action} {name}", flush=True)
                saved = next((d for d in previous["documents"] if d["filename"] == name), None
                             ) if previous else None
                if args.query_report and (not saved or saved["ast_sha256"] != item["ast_sha256"]):
                    raise ValueError("Read-only reuse requires unchanged saved source ASTs")
                if saved and saved["sha256"] != item["sha256"]:
                    raise ValueError("Already indexed PDF source differs")
                if saved and saved["ast_sha256"] != item["ast_sha256"]:
                    if not args.override_report:
                        raise ValueError("Already indexed AST differs without a correction report")
                    # Keep old vectors/evidence intact, index this corrected AST
                    # under a fresh UUID, and scope every query to the new UUID.
                    report["retained_superseded_vectors"].append(saved)
                    saved = None
                doc = Document(**({"id": saved["document_id"]} if saved else {}),
                               original_filename=name, storage_key=f"official-{name}",
                               size_bytes=(args.corpus / name).stat().st_size,
                               sha256=item["sha256"], status=DocumentStatus.READY,
                               title=ast["title"], page_count=len(ast["pages"]),
                               authors_json=json.dumps(ast["authors"], ensure_ascii=False))
                session.add(doc)
                session.flush()
                (settings.parsed_dir / f"{doc.id}.json").write_text(
                    json.dumps(ast, ensure_ascii=False), encoding="utf-8",
                )
                expected = replace_document_chunks(session, doc.id, ast)
                session.commit()
                if saved:
                    actual = index.document_point_count(doc.id)
                    if actual != saved["chunks"]:
                        raise ValueError("Saved point count differs; do not silently reindex")
                    # UUIDs must match existing vector payloads, not newly created SQLite rows.
                    offset = None
                    restored = []
                    while True:
                        points, offset = index.client.scroll(
                            collection_name=index.collection_name, limit=256, offset=offset,
                            scroll_filter=models.Filter(must=[models.FieldCondition(
                                key="document_id", match=models.MatchValue(value=doc.id))]),
                            with_payload=True, with_vectors=False,
                        )
                        restored.extend(points)
                        if offset is None:
                            break
                    verify_restored_chunks(restored, build_chunk_drafts(ast))
                    session.execute(delete(DocumentChunk).where(
                        DocumentChunk.document_id == doc.id,
                    ))
                    positions = {(d.section, d.text, tuple(d.block_ids)): d.chunk_index
                                 for d in build_chunk_drafts(ast)}
                    restored.sort(key=lambda point: positions.get((
                        point.payload["section"], point.payload["text"],
                        tuple(point.payload["block_ids"])), len(positions)))
                    for n, point in enumerate(restored):
                        p = point.payload
                        session.add(DocumentChunk(id=str(point.id), document_id=doc.id,
                            chunk_index=n, page_number=p["page_number"],
                            block_ids_json=json.dumps(p["block_ids"]),
                            bbox_json=json.dumps(p["bbox"]), section=p["section"],
                            text=p["text"], char_count=len(p["text"])))
                    session.commit()
                    report["resumed_documents"].append(name)
                else:
                    actual = index.index_document(session, doc.id)
                if (expected <= 0 or actual != expected
                        or index.document_point_count(doc.id) != actual):
                    raise ValueError(f"Incomplete isolated vector index: {name}")
                ids[name], asts[name] = doc.id, ast
                report["documents"].append({k: v for k, v in item.items() if k != "ast"}
                                           | {"document_id": doc.id, "chunks": actual})
                snapshot(path, report)
            reranker = RecordingReranker(get_reranker())
            retriever = HybridRetriever(session, vector_index=index, reranker=reranker)
            for n, case in enumerate(cases, 1):
                started = perf_counter()
                result = {**case, "failures": []}
                print(f"Retrieving [{n}/{len(cases)}] {case['case_id']}", flush=True)
                try:
                    evidence = retriever.search(case["question"], [ids[case["filename"]]],
                                                args.top_k)
                    result["candidate_hit"] = any(probe_hit(e, case["gold"])
                                                  for e in reranker.candidates)
                    result["final_hit"] = any(probe_hit(e, case["gold"]) for e in evidence)
                    result["first_hit_rank"] = next((i for i, e in enumerate(evidence, 1)
                                                     if probe_hit(e, case["gold"])), None)
                    if not result["final_hit"]:
                        result["failures"].append("gold_quote_not_in_final_top_k_on_source_page")
                    if not evidence or any(e.retrieval_mode != "reranked"
                                           or e.recall_mode != "hybrid" for e in evidence):
                        result["failures"].append("not_real_hybrid_reranked_chain")
                    if any(not anchor_valid(e, ids[case["filename"]], asts[case["filename"]])
                           for e in evidence):
                        result["failures"].append("invalid_or_out_of_scope_source_anchor")
                    result["evidence"] = [e.model_dump(mode="json") for e in evidence]
                    result["candidates"] = [e.model_dump(mode="json") for e in reranker.candidates]
                except Exception as exc:
                    result["failures"].append(f"retrieval_error:{type(exc).__name__}")
                result["elapsed_seconds"] = round(perf_counter() - started, 3)
                result["status"] = "failed" if result["failures"] else "passed"
                report["cases"].append(result)
                snapshot(path, report)
        report["summary"] = {"documents_indexed": len(report["documents"]),
                             "cases": len(report["cases"]),
                             "passed": sum(c["status"] == "passed" for c in report["cases"]),
                             "failed": sum(c["status"] == "failed" for c in report["cases"])}
        report["status"] = "completed"
    except Exception as exc:
        report["status"], report["error_type"] = "error", type(exc).__name__
        raise
    finally:
        report["dense_embedding_batches"] = embedder.calls
        report["reranker_invocations"] = reranker.calls if "reranker" in locals() else 0
        snapshot(path, report)
        index.client.close()
        embedding_client.close()
        engine.dispose()
    return report["summary"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["corpus", "manifest", "probes", "parse-report", "output-dir"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--filenames", nargs="+")
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--override-report", type=Path)
    reuse = parser.add_mutually_exclusive_group()
    reuse.add_argument("--resume-report", type=Path)
    reuse.add_argument("--query-report", type=Path,
                       help="Read-only reuse of a completed isolated index; fresh model queries")
    parser.add_argument("--embedding-url", default="http://127.0.0.1:8001")
    parser.add_argument("--reranker-url", default="http://127.0.0.1:8002")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:7333")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error("top-k must be between 1 and 20")
    result = run(args)
    print(json.dumps(result))
    raise SystemExit(0 if result.get("failed", 0) == 0 else 1)


if __name__ == "__main__":
    main()
