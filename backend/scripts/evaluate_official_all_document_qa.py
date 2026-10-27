"""One real, source-pinned sample per organizer PDF, with a shared bounded cloud budget.

Only isolated indexes are used; previous indexes are read-only. Complete fresh
ASTs are required and compared to restored chunk payloads before reuse. Keyword
checks remain preliminary, never scientific source-review verdicts.
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import fitz
import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.harness import ResearchAgent  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.schemas.agent import AskRequest  # noqa: E402
from app.services.chunking import build_chunk_drafts, replace_document_chunks  # noqa: E402
from app.services.vector_index import QdrantVectorIndex  # noqa: E402
from scripts.evaluate_official_first_round import (  # noqa: E402
    OneAttemptProvider,
    ReadOnlyOfficialIndex,
    restore_saved_documents,
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=\w)-\s+(?=\w)", "", text)
    return re.sub(r"\s+", "", text).casefold()


def write_snapshot(path, report):
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def collect_inputs(corpus, manifest_path, probe_path, answers_path, parse_reports):
    manifest = json.loads(manifest_path.read_bytes())
    entries = {row["filename"]: row for row in manifest["downloads"]}
    answers = json.loads(answers_path.read_bytes())
    methods = json.loads(probe_path.read_bytes())["probes"]
    samples = [{**row, "answer_groups": answers["answer_groups_by_filename"][row["filename"]]}
               for row in methods] + answers["extra_probes"]
    if (len(entries) != 39 or len(manifest["downloads"]) != 39 or len(samples) != 39
            or len({row["filename"] for row in samples}) != 39
            or {row["filename"] for row in samples} != set(entries)):
        raise ValueError("One explicit independent sample is required for each of 39 PDFs")
    records = {}
    for report_path in parse_reports:
        report = json.loads(report_path.read_bytes())
        for row in report["documents"]:
            if row["filename"] in records:
                raise ValueError("Overlapping fresh parser reports")
            if (row.get("parsed_page_count") != row.get("page_count")
                    or row.get("status") not in {"passed", "failed"}):
                raise ValueError("Every source must have a complete fresh AST, not an error")
            artifact_dir = row["artifacts_directory"]
            if Path(artifact_dir).name != artifact_dir:
                raise ValueError("Unsafe AST artifact path")
            ast_path = (report_path.parent / artifact_dir / "artifacts/parsed.json").resolve()
            if not ast_path.is_relative_to(report_path.parent.resolve()):
                raise ValueError("AST path escapes the parser report directory")
            records[row["filename"]] = (row, ast_path, report_path)
    if set(records) != set(entries):
        raise ValueError("Fresh parsing must cover all 39 source files before cloud calls")
    result = []
    for sample in samples:
        name, page = sample["filename"], sample["page"]
        entry = entries[name]
        path = (corpus / name).resolve()
        if Path(name).name != name or path.parent != corpus.resolve():
            raise ValueError("Unsafe organizer filename")
        row, ast_path, parse_report = records[name]
        if digest(path) != entry["sha256"] or row["source_sha256"] != entry["sha256"]:
            raise ValueError("Source identity mismatch")
        ast = json.loads(ast_path.read_bytes())
        if [p["page_number"] for p in ast["pages"]] != list(range(1, row["page_count"] + 1)):
            raise ValueError("AST physical page sequence is incomplete")
        if (not sample["answer_groups"] or any(not group or any(not isinstance(word, str)
                or not word.strip() for word in group) for group in sample["answer_groups"])):
            raise ValueError("Every sample needs nonempty answer fields")
        with fitz.open(path) as native:
            if not 1 <= page <= len(native):
                raise ValueError("Independent gold page is outside the PDF")
            text = native[page - 1].get_text()
            visual = sample.get("source_kind") == "independent_visual_scan_page"
            if visual:
                if name != "1706.03762v1_img.pdf" or text.strip():
                    raise ValueError("Visual scan gold is restricted to the pinned no-text file")
            elif normalized(sample["quote"]) not in normalized(text):
                raise ValueError(f"Independent gold quote is absent from native source: {name}")
        result.append({**sample, "case_id": "sample:" + name, "sha256": entry["sha256"],
                       "ast": ast, "ast_sha256": digest(ast_path),
                       "parse_report_sha256": digest(parse_report),
                       "parser_gate_passed": row["status"] == "passed",
                       "parser_failures": row["failures"]})
    return result


def validate_index_report(report):
    if report.get("production_library_modified") is not False:
        raise ValueError("Never reuse an arbitrary or production index")
    collection = report.get("temporary_vector_collection_actual",
                            report.get("temporary_vector_collection", ""))
    pattern = r"(?:official_first_round|official_remaining_bge)_[a-f0-9]{32}"
    if not re.fullmatch(pattern + r"_semantic_[a-f0-9]{10}_1024", collection):
        raise ValueError("Only known isolated official evaluator namespaces may be reused")
    if report.get("evaluation_kind") not in {
        "official_local_bge_retrieval_not_generation",
        "official_original_questions_production_ask_harness",
    } or report.get("status") in {"running", "error"}:
        raise ValueError("Only a completed known evaluator report can supply payloads")
    return collection


def signature(page, section, text, ids, box):
    return (page, section, text, tuple(ids), tuple(box or []))


class VerifiedAstMismatch(ValueError):
    """Only verified content drift may lead to a separate, freshly built index."""

    def __init__(self, expected, actual):
        super().__init__("Restored vector payloads differ from the fresh verified AST")
        self.details = {"current_chunks": sum(expected.values()),
                        "saved_chunks": sum(actual.values()),
                        "missing_current_chunks": sum((expected - actual).values()),
                        "obsolete_saved_chunks": sum((actual - expected).values())}


def verify_chunks(session, doc_id, ast):
    expected = Counter(signature(d.page_number, d.section, d.text, d.block_ids, d.bbox)
                       for d in build_chunk_drafts(ast))
    actual = Counter(signature(row.page_number, row.section, row.text,
                               json.loads(row.block_ids_json),
                               json.loads(row.bbox_json) if row.bbox_json else None)
                     for row in session.scalars(select(DocumentChunk).where(
                         DocumentChunk.document_id == doc_id)))
    if not expected:
        raise ValueError("The current AST has no indexable evidence")
    if expected != actual:
        raise VerifiedAstMismatch(expected, actual)


def index_current_ast(session, index, case, source_path, restored_doc=None):
    """Write only a fresh namespace and this evaluator's disposable SQLite rows."""
    if isinstance(index, ReadOnlyOfficialIndex):
        raise ValueError("Never rebuild a saved read-only namespace")
    ast = case["ast"]
    doc = restored_doc or Document(original_filename=case["filename"],
        storage_key="test-" + case["filename"], sha256=case["sha256"],
        size_bytes=source_path.stat().st_size, title=ast["title"],
        status=DocumentStatus.READY, page_count=len(ast["pages"]),
        authors_json=json.dumps(ast["authors"]))
    if restored_doc is None:
        session.add(doc)
        session.flush()
    expected = replace_document_chunks(session, doc.id, ast)
    session.commit()
    actual = index.index_document(session, doc.id)
    if not actual or actual != expected:
        raise ValueError("Fresh isolated index is incomplete")
    verify_chunks(session, doc.id, ast)
    return doc.id


def source_anchor_valid(anchor, ast, drafts):
    if not 1 <= anchor.page_number <= len(ast["pages"]):
        return False
    box = anchor.bbox
    page = ast["pages"][anchor.page_number - 1]
    if (not box or len(box) != 4 or not all(math.isfinite(value) for value in box)
            or not 0 <= box[0] < box[2] <= page["width"] + 1
            or not 0 <= box[1] < box[3] <= page["height"] + 1):
        return False
    return any(
        draft.page_number == anchor.page_number and draft.bbox
        and set(anchor.block_ids).intersection(draft.block_ids)
        and normalized(anchor.quote) in normalized(draft.text)
        and min(box[2], draft.bbox[2]) > max(box[0], draft.bbox[0])
        and min(box[3], draft.bbox[3]) > max(box[1], draft.bbox[1])
        for draft in drafts
    )


def check_response(case, response, doc_id):
    anchors = {item.evidence_id: item for item in response.evidence}
    drafts = build_chunk_drafts(case["ast"])
    valid = bool(anchors) and all(item.document_id == doc_id and item.bbox and item.block_ids
                                 and item.recall_mode == "hybrid"
                                 and item.retrieval_mode == "reranked"
                                 and source_anchor_valid(item, case["ast"], drafts)
                                 for item in anchors.values())
    facts = []
    for group in case["answer_groups"]:
        facts.append(any(any(normalized(word) in normalized(claim.text) for word in group)
                         and any(key in anchors and anchors[key].page_number == case["page"]
                                 and normalized(case["quote"]) in normalized(anchors[key].quote)
                                 for key in claim.evidence_ids)
                         for claim in response.claims))
    failures = []
    if not valid:
        failures.append("not_valid_scoped_hybrid_reranked_evidence")
    if not response.claims or any(not claim.evidence_ids or set(claim.evidence_ids) - set(anchors)
                                  for claim in response.claims):
        failures.append("missing_valid_claim_citation")
    if response.provider != "deepseek" or response.insufficient_evidence:
        failures.append("no_complete_real_model_answer")
    if not all(facts):
        failures.append("missing_answer_field_or_required_independent_source")
    return {"facts": facts, "failures": failures,
            "status": "failed" if failures else "machine_passed_pending_source_review"}


def prior_call_count(prior_path, case_count, limit):
    """Validate a finite fresh-round cap or the original shared-checkpoint cap."""
    prior_count = 0
    if prior_path is not None:
        prior = json.loads(prior_path.read_bytes())
        if (prior.get("selected_case_ids") != ["bleu-improvement", "gpt-parameter-evolution",
                                              "context-evolution"]
                or prior.get("model_calls") != 3 or prior.get("automatic_retries") is not False):
            raise ValueError("Historical budget accounting requires the preserved checkpoint")
        prior_count = prior["model_calls"]
    # New authorized rounds can have their own finite cap without reporting
    # yesterday's calls as calls made in this new round. The original 42-call
    # checkpoint workflow is still supported, never overwritten or relabelled.
    if limit < 1 or case_count + prior_count > limit:
        raise ValueError("Selected cloud tasks exceed this explicit batch authorization")
    return prior_count


async def run(args):
    if args.output_dir.exists():
        raise ValueError("Never overwrite any previous validation evidence")
    cases = collect_inputs(args.corpus, args.manifest, args.probes, args.answers,
                           args.parse_reports)
    prior_count = prior_call_count(args.prior_calls, len(cases), args.max_total_calls)
    args.output_dir.mkdir(parents=True)
    public_cases = [{key: value for key, value in case.items() if key != "ast"} for case in cases]
    (args.output_dir / "source-gold.json").write_text(
        json.dumps(public_cases, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.prepare_only:
        return {"status": "prepared", "documents": len(cases), "model_calls": 0}
    if not args.allow_cloud:
        raise ValueError("Real generation requires explicit --allow-cloud authorization")
    provider = get_llm_provider()
    if provider.name != "deepseek" or not settings.vector_search_enabled:
        raise ValueError("Real configured DeepSeek and vector search are required")
    # Existing namespaces are checked before constructing any new test namespace.
    saved = {}
    for path in args.index_reports:
        previous = json.loads(path.read_bytes())
        collection = validate_index_report(previous)
        for item in previous["documents"]:
            name = item["filename"]
            if name not in {case["filename"] for case in cases}:
                continue
            if name in saved:
                raise ValueError("A public document may not have ambiguous reused indexes")
            saved[name] = (collection, item)
    parsed_dir = args.output_dir / "parsed"
    parsed_dir.mkdir()
    settings.parsed_dir = parsed_dir
    settings.embedding_batch_size = 32
    report = {
        "evaluation_kind": "official_all_39_one_sample_host_generation",
        "status": "preparing_indexes", "model_calls": 0, "prior_authorized_calls": prior_count,
        "max_total_authorized_calls": args.max_total_calls, "automatic_retries": False,
        "production_library_modified": False, "public_excerpt_only": True,
        "parser_calls": 0, "original_parser_reports_reused": True,
        "provider": provider.name, "model": provider.model,
        "dataset_sha256": digest(args.answers), "source_gold_sha256": digest(
            args.output_dir / "source-gold.json"),
        "prior_calls_report_sha256": digest(args.prior_calls) if args.prior_calls else None,
        "inputs_sha256": {str(path): digest(path) for path in [args.manifest, args.probes,
                                *args.parse_reports, *args.index_reports]},
        "runtime_code_sha256": {str(path.relative_to(Path(__file__).parents[1])): digest(path)
                                for path in [Path(__file__), *sorted(
                                    (Path(__file__).parents[1] / "app").rglob("*.py"))]},
        "scientific_source_review": "pending_separate_complete_page_review",
        "sparse_note": "BGE learned dense + hash sparse/BM25; not learned BGE sparse",
        "documents": [], "cases": [],
    }
    output = args.output_dir / "report.json"
    write_snapshot(output, report)
    client = httpx.Client(trust_env=False, timeout=settings.embedding_timeout_seconds)
    embedder = OpenAICompatibleEmbeddingProvider(model=settings.embedding_model,
        dimensions=settings.embedding_dimensions, base_url=settings.embedding_base_url,
        client=client)
    fresh_base = "official_all_corpus_" + uuid4().hex
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    indexes, routes = {}, {}
    try:
        with Session(engine, expire_on_commit=False) as session:
            for case in cases:
                name, ast = case["filename"], case["ast"]
                print("Preparing verified full-document index: " + name, flush=True)
                drift = None
                if name in saved:
                    collection, record = saved[name]
                    if record["sha256"] != case["sha256"]:
                        raise ValueError("Saved index source identity differs")
                    if collection not in indexes:
                        suffix = "_" + embedder.index_namespace + "_1024"
                        if not collection.endswith(suffix):
                            raise ValueError("Saved vector model namespace differs")
                        settings.vector_collection = collection.removesuffix(suffix)
                        indexes[collection] = ReadOnlyOfficialIndex(embedder=embedder)
                    index = indexes[collection]
                    saved_record = {"filename": name, "id": record.get("id", record.get(
                        "document_id")), "sha256": case["sha256"], "title": ast["title"],
                        "pages": len(ast["pages"]), "chunks": record["chunks"]}
                    restore_saved_documents(session, index, [saved_record])
                    doc_id = saved_record["id"]
                    try:
                        verify_chunks(session, doc_id, ast)
                    except VerifiedAstMismatch as exc:
                        drift = {**exc.details, "previous_collection": collection,
                                 "previous_document_id": doc_id}
                        print("Verified AST changed; keeping saved index untouched and "
                              "building a new isolated index: " + name, flush=True)
                    action = "verified_read_only_reuse"
                if name not in saved or drift is not None:
                    settings.vector_collection = fresh_base
                    if fresh_base not in indexes:
                        indexes[fresh_base] = QdrantVectorIndex(embedder=embedder)
                        if indexes[fresh_base].client.collection_exists(
                            indexes[fresh_base].collection_name):
                            raise ValueError("Fresh isolated namespace unexpectedly exists")
                    index = indexes[fresh_base]
                    restored = session.get(Document, doc_id) if drift is not None else None
                    doc_id = index_current_ast(session, index, case, args.corpus / name, restored)
                    action = ("changed_ast_new_isolated_index" if drift is not None
                              else "new_full_document_isolated_index")
                (parsed_dir / f"{doc_id}.json").write_text(
                    json.dumps(ast, ensure_ascii=False), encoding="utf-8")
                routes[name] = (doc_id, index)
                report["documents"].append({"filename": name, "id": doc_id,
                    "origin": "official", "sha256": case["sha256"], "title": ast["title"],
                    "pages": len(ast["pages"]), "ast_sha256": case["ast_sha256"],
                    "index_action": action, "collection": index.collection_name,
                    "saved_ast_drift": drift,
                    "parser_gate_passed": case["parser_gate_passed"]})
                write_snapshot(output, report)
            report["status"] = "generating"
            write_snapshot(output, report)
            for n, case in enumerate(cases, 1):
                if report["model_calls"] + prior_count >= args.max_total_calls:
                    raise ValueError("Authorized batch budget exhausted")
                doc_id, index = routes[case["filename"]]
                once = OneAttemptProvider(provider, require_full_retrieval=True)
                started = perf_counter()
                result = {"case_id": case["case_id"], "filename": case["filename"],
                          "question": case["question"], "batch_number": (n - 1) // 8 + 1,
                          "status": "failed", "source_review": "pending"}
                print(f"Generating once [{n}/39]: {case['filename']}", flush=True)
                try:
                    with patch("app.services.vector_index.get_vector_index", return_value=index):
                        response = await ResearchAgent(session, provider=once).ask(AskRequest(
                            question=case["question"], document_ids=[doc_id], top_k=18))
                    result.update(check_response(case, response, doc_id))
                    result["response"] = response.model_dump(mode="json")
                except Exception as exc:
                    # Do not echo cloud exception bodies, keys or request headers.
                    result["failures"] = ["execution_error:" + type(exc).__name__]
                result.update(model_calls=once.calls, raw_generated=once.raw,
                              raw_provider_output=once.raw_provider_output,
                              generation_request=once.request,
                              elapsed_seconds=round(perf_counter() - started, 3))
                report["model_calls"] += once.calls
                report["cases"].append(result)
                write_snapshot(output, report)
                print(json.dumps({"filename": case["filename"], "status": result["status"],
                                  "failures": result["failures"]}), flush=True)
                # Provider failure is a batch incident, not permission to spend
                # all remaining calls or retry a failed question.
                if once.calls and (once.raw is None or result.get("response", {}).get(
                        "provider") != "deepseek"):
                    report["status"] = "stopped_provider_failure"
                    break
            else:
                report["status"] = "completed_pending_source_review"
    except Exception as exc:
        report["status"], report["error_type"] = "error_before_or_during_batch", type(exc).__name__
        raise
    finally:
        report["summary"] = {
            "documents_prepared": len(report["documents"]), "cases_executed": len(report["cases"]),
            "machine_passed": sum(row["status"] == "machine_passed_pending_source_review"
                                  for row in report["cases"]),
            "failed": sum(row["status"] == "failed" for row in report["cases"]),
            "not_run": 39 - len(report["cases"]), "new_cloud_calls": report["model_calls"],
            "total_authorized_batch_calls_used": prior_count + report["model_calls"],
        }
        write_snapshot(output, report)
        for index in indexes.values():
            index.client.close()
        client.close()
        engine.dispose()
    return report["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "manifest", "probes", "answers", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--prior-calls", type=Path,
                        help="Optional preserved checkpoint for historical shared-budget runs")
    parser.add_argument("--parse-reports", type=Path, nargs="+", required=True)
    parser.add_argument("--index-reports", type=Path, nargs="+", required=True)
    parser.add_argument("--max-total-calls", type=int, default=42)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args))))
