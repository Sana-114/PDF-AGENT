"""Retest unchanged public corpus questions once using verified read-only indexes.

Preflight every selected source/index before cloud generation. Preserve original
reports, raw output, rejected claims and failed attempts; never retry automatically.
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.harness import ResearchAgent  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.schemas.agent import AskRequest  # noqa: E402
from app.services.chunking import build_chunk_drafts  # noqa: E402
from scripts.evaluate_official_all_document_qa import (  # noqa: E402
    check_response,
    collect_inputs,
    digest,
    verify_chunks,
    write_snapshot,
)
from scripts.evaluate_official_first_round import (  # noqa: E402
    OneAttemptProvider,
    ReadOnlyOfficialIndex,
    restore_saved_documents,
)


def select_originals(previous, inputs, filenames, *, pending_only=False, retest_executed=False):
    """Fail closed on duplicate selections or changed source/question/AST identities."""
    if pending_only and retest_executed:
        raise ValueError("Choose pending continuation or explicit executed-case retest")
    if (
        previous.get("evaluation_kind") != "official_all_39_one_sample_host_generation"
        or previous.get("status")
        != (
            "stopped_provider_failure"
            if pending_only or retest_executed
            else "completed_pending_source_review"
        )
        or previous.get("production_library_modified") is not False
    ):
        raise ValueError("Require the preserved isolated official corpus run")
    documents = {row["filename"]: row for row in previous["documents"]}
    cases = {row["filename"]: row for row in previous["cases"]}
    probes = {row["filename"]: row for row in inputs}
    if (
        len(documents) != 39
        or len(previous["documents"]) != 39
        or len(probes) != 39
        or len(inputs) != 39
        or len(cases) != len(previous["cases"])
        or set(cases) - set(probes)
        or (not pending_only and not retest_executed and len(cases) != 39)
    ):
        raise ValueError("Require exactly 39 unique original inputs")
    for name, case in cases.items():
        if case["question"] != probes[name]["question"]:
            raise ValueError("Original source, AST or question changed")
    pending = [name for name in probes if name not in cases]
    names = filenames if filenames is not None else (pending if pending_only else list(probes))
    if not names or len(names) != len(set(names)) or set(names) - set(probes):
        raise ValueError("Choose unique existing original questions")
    if pending_only and set(names) - set(pending):
        raise ValueError("Pending-only continuation must never repeat an executed question")
    if retest_executed and (filenames is None or set(names) - set(cases)):
        raise ValueError("Explicitly name only previously executed questions")
    result = []
    for name in names:
        doc, probe = documents[name], probes[name]
        if (
            doc["sha256"] != probe["sha256"]
            or doc["ast_sha256"] != probe["ast_sha256"]
            or (name in cases and cases[name]["question"] != probe["question"])
        ):
            raise ValueError("Original source, AST or question changed")
        collection = doc["collection"]
        if not re.fullmatch(
            r"official_(?:remaining_bge|first_round|all_corpus)_[a-f0-9]{32}"
            r"_semantic_[a-f0-9]{10}_1024",
            collection,
        ):
            raise ValueError("Never access a production or arbitrary vector namespace")
        result.append((probe, doc))
    return result


async def run(args):
    if args.output_dir.exists():
        raise ValueError("Never overwrite previous verification artifacts")
    if not args.allow_cloud:
        raise ValueError("Real generation requires explicit --allow-cloud")
    previous = json.loads(args.report.read_bytes())
    if previous["dataset_sha256"] != digest(args.answers):
        raise ValueError("Original answer fields may not be changed")
    gold_path = args.report.parent / "source-gold.json"
    if digest(gold_path) != previous["source_gold_sha256"]:
        raise ValueError("Original independent source gold changed")
    inputs = collect_inputs(
        args.corpus, args.manifest, args.probes, args.answers, args.parse_reports
    )
    selected = select_originals(
        previous,
        inputs,
        args.filenames,
        pending_only=args.pending_only,
        retest_executed=getattr(args, "retest_executed", False),
    )
    if (
        not settings.vector_search_enabled
        or settings.embedding_model != "BAAI/bge-m3"
        or settings.reranker_provider != "tei"
    ):
        raise ValueError("Require real BGE retrieval and TEI reranking")
    provider = get_llm_provider()
    if provider.name != "deepseek" or provider.model != "deepseek-flash":
        raise ValueError("Require the configured real DeepSeek Flash provider")
    args.output_dir.mkdir(parents=True)
    settings.parsed_dir = args.output_dir / "parsed"
    settings.parsed_dir.mkdir()
    backend = Path(__file__).parents[1]
    report = {
        "evaluation_kind": "official_unchanged_corpus_questions_readonly_generation_retest",
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "preparing_indexes",
        "model_calls": 0,
        "automatic_retries": False,
        "parser_calls": 0,
        "authorization": "user_expanded_public_excerpt_verification_2026-10-11",
        "one_generation_per_selected_question": True,
        "pending_only_continuation": args.pending_only,
        "explicit_executed_case_retest": getattr(args, "retest_executed", False),
        "public_excerpt_only": True,
        "production_library_modified": False,
        "vector_index_modified": False,
        "original_report_sha256": digest(args.report),
        "provider": provider.name,
        "model": provider.model,
        "dataset_sha256": digest(args.answers),
        "source_gold_sha256": digest(gold_path),
        "selected_filenames": [probe["filename"] for probe, _ in selected],
        "input_sha256": {
            str(path): digest(path)
            for path in [args.manifest, args.probes, args.answers, *args.parse_reports]
        },
        "runtime_code_sha256": {
            str(path.relative_to(backend)): digest(path)
            for path in [Path(__file__), *sorted((backend / "app").rglob("*.py"))]
        },
        "scientific_source_review": "pending_independent_review_of_all_published_claims",
        "documents": [],
        "cases": [],
    }
    output = args.output_dir / "report.json"
    write_snapshot(output, report)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    indexes, routes = {}, {}
    try:
        with Session(engine, expire_on_commit=False) as session:
            # Complete all index/source preflight before the first billed call.
            for probe, doc in selected:
                collection = doc["collection"]
                if collection not in indexes:
                    settings.vector_collection = collection.split("_semantic_")[0]
                    index = ReadOnlyOfficialIndex()
                    indexes[collection] = index
                    if index.collection_name != collection or not index.client.collection_exists(
                        collection
                    ):
                        raise ValueError("Saved vector namespace unavailable or model changed")
                index = indexes[collection]
                restore_saved_documents(
                    session, index, [{**doc, "chunks": len(build_chunk_drafts(probe["ast"]))}]
                )
                verify_chunks(session, doc["id"], probe["ast"])
                (settings.parsed_dir / f"{doc['id']}.json").write_text(
                    json.dumps(probe["ast"], ensure_ascii=False), encoding="utf-8"
                )
                routes[probe["filename"]] = (doc["id"], index)
                report["documents"].append({**doc, "index_action": "verified_read_only_reuse"})
                write_snapshot(output, report)
            report["status"] = "generating"
            write_snapshot(output, report)
            for n, (probe, _) in enumerate(selected, 1):
                doc_id, index = routes[probe["filename"]]
                once = OneAttemptProvider(provider, require_full_retrieval=True)
                started = perf_counter()
                row = {
                    "case_id": probe["case_id"],
                    "filename": probe["filename"],
                    "question": probe["question"],
                    "status": "failed",
                    "failures": [],
                    "source_review": "pending",
                }
                print(f"Generating once [{n}/{len(selected)}]: {probe['filename']}", flush=True)
                try:
                    with patch("app.services.vector_index.get_vector_index", return_value=index):
                        response = await ResearchAgent(session, provider=once).ask(
                            AskRequest(question=probe["question"], document_ids=[doc_id], top_k=18)
                        )
                    row.update(check_response(probe, response, doc_id))
                    row["response"] = response.model_dump(mode="json")
                except Exception as exc:
                    row["failures"] = ["execution_error:" + type(exc).__name__]
                row.update(
                    model_calls=once.calls,
                    raw_generated=once.raw,
                    raw_provider_output=once.raw_provider_output,
                    generation_request=once.request,
                    elapsed_seconds=round(perf_counter() - started, 3),
                )
                report["cases"].append(row)
                report["model_calls"] += once.calls
                write_snapshot(output, report)
                print(
                    json.dumps(
                        {
                            "filename": row["filename"],
                            "status": row["status"],
                            "failures": row["failures"],
                        }
                    ),
                    flush=True,
                )
                if once.calls and (
                    once.raw is None or row.get("response", {}).get("provider") != "deepseek"
                ):
                    report["status"] = "stopped_provider_failure"
                    break
            else:
                report["status"] = "completed_pending_source_review"
    except Exception as exc:
        report["status"], report["error_type"] = "error_before_or_during_batch", type(exc).__name__
        raise
    finally:
        report["summary"] = {
            "selected": len(selected),
            "executed": len(report["cases"]),
            "machine_passed": sum(
                row["status"] == "machine_passed_pending_source_review" for row in report["cases"]
            ),
            "failed": sum(row["status"] == "failed" for row in report["cases"]),
            "not_run": len(selected) - len(report["cases"]),
            "new_cloud_calls": report["model_calls"],
        }
        write_snapshot(output, report)
        for index in indexes.values():
            index.client.close()
        engine.dispose()
    return report["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "manifest", "probes", "answers", "report", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--parse-reports", type=Path, nargs="+", required=True)
    parser.add_argument("--filenames", nargs="+")
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Continue only unexecuted questions in a stopped isolated batch",
    )
    parser.add_argument(
        "--retest-executed",
        action="store_true",
        help="Explicit subset of already executed questions from a stopped batch",
    )
    parser.add_argument("--allow-cloud", action="store_true")
    print(json.dumps(asyncio.run(run(parser.parse_args()))))
