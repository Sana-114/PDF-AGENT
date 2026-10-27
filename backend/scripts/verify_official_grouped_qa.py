"""Eight independent BLOOM credit questions through actual BGE/TEI/DeepSeek API.

Public excerpts only, one attempt per question; raw failed answers are retained.
This is a bounded current-source ASGI acceptance, not a whole-corpus score.
"""

import argparse
import json
import os
import re
import shutil
import time
import unicodedata
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from verify_official_output_features import configure


def plain(text):
    return "".join(c.casefold() for c in unicodedata.normalize("NFKD", text) if c.isalnum())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--reuse-index-from", type=Path)
    args = parser.parse_args()
    if not args.allow_cloud:
        raise ValueError("Requires explicit public-excerpt cloud allowance")
    fixture = args.fixture.resolve()
    project = Path(__file__).resolve().parents[2]
    if not fixture.is_relative_to(TEMP_ROOT):
        raise ValueError("Requires the audited isolated BLOOM fixture")
    old = json.loads((fixture / "report.json").read_bytes())
    ast_path = fixture / "fresh-parsed.json"
    manifest = json.loads(
        (project / "docs/evidence/official-grouped-reader-acceptance-2026-10-18.json").read_bytes()
    )
    relative = ast_path.relative_to(project).as_posix()
    expected = next(row["sha256"] for row in manifest["artifacts"] if row["path"] == relative)
    if digest(ast_path) != expected:
        raise ValueError("Saved public AST changed")
    source = fixture / "uploads" / (old["document_id"] + ".pdf")
    if digest(source) != "8033c3e611aaa120cb7294085ebd6ee297ffae9ec02393b1c4f1593d7e6ee065":
        raise ValueError("Official BLOOM source identity changed")
    root = isolated_output(args.output_dir)
    reuse = None
    if args.reuse_index_from:
        saved = args.reuse_index_from.resolve()
        if not saved.is_relative_to(TEMP_ROOT) or saved.name != "report.json":
            raise ValueError("Only a prior isolated grouped-QA report is allowed")
        reuse = json.loads(saved.read_bytes())
        collection = reuse.get("temporary_vector_collection", "")
        if (
            reuse.get("production_library_modified") is not False
            or reuse.get("source_sha256") != digest(source)
            or reuse.get("ast_sha256") != digest(ast_path)
            or not re.fullmatch(
                r"official_grouped_qa_[a-f0-9]{32}_semantic_[a-f0-9]{10}_1024", collection
            )
        ):
            raise ValueError("Saved public index identity/scope changed")
        shutil.copy2(saved.parent / "acceptance.db", root / "acceptance.db")
    configure(root)
    os.environ.update(
        VECTOR_SEARCH_ENABLED="true",
        EMBEDDING_PROVIDER="openai-compatible",
        EMBEDDING_MODEL="BAAI/bge-m3",
        EMBEDDING_DIMENSIONS="1024",
        EMBEDDING_BASE_URL="http://127.0.0.1:8001/v1",
        EMBEDDING_BATCH_SIZE="16",
        QDRANT_URL="http://127.0.0.1:7333",
        VECTOR_TIMEOUT_SECONDS="15",
        VECTOR_COLLECTION="official_grouped_qa_" + uuid4().hex,
        RERANKER_PROVIDER="tei",
        RERANKER_BASE_URL="http://127.0.0.1:8002",
        RERANKER_CANDIDATE_K="24",
        NO_PROXY="localhost,127.0.0.1,::1",
    )
    if reuse:
        os.environ["VECTOR_COLLECTION"] = reuse["temporary_vector_collection"].split("_semantic_")[
            0
        ]

    from evaluate_official_all_document_qa import source_anchor_valid
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import SessionLocal, init_db
    from app.embeddings.factory import get_embedding_provider
    from app.llm.factory import get_llm_provider
    from app.main import app
    from app.models.chunk import DocumentChunk
    from app.models.document import Document, DocumentStatus
    from app.schemas.agent import AskResponse
    from app.services.chunking import build_chunk_drafts, replace_document_chunks
    from app.services.vector_index import QdrantVectorIndex

    provider = get_llm_provider()
    if provider.name != "deepseek" or not settings.llm_api_key:
        raise ValueError("Actual configured DeepSeek required; do not substitute extraction")
    settings.ensure_directories()
    init_db()
    ast = json.loads(ast_path.read_bytes())
    doc_id = reuse["document_id"] if reuse else str(uuid4())
    shutil.copy2(source, root / "uploads" / (doc_id + ".pdf"))
    shutil.copy2(ast_path, root / "parsed" / (doc_id + ".json"))
    with SessionLocal() as db:
        if reuse:
            count = len(
                db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc_id)).all()
            )
        else:
            db.add(
                Document(
                    id=doc_id,
                    original_filename="2211.05100v4.pdf",
                    storage_key=doc_id + ".pdf",
                    title=ast["title"],
                    authors_json=json.dumps(ast["authors"]),
                    sha256=digest(source),
                    size_bytes=source.stat().st_size,
                    status=DocumentStatus.READY,
                    page_count=73,
                    parser_name="pymupdf",
                    parser_version=old["parser"],
                )
            )
            db.commit()
            count = replace_document_chunks(db, doc_id, ast)
            db.commit()
    tasks = [
        (
            "collective-credit",
            "BLOOM 论文首页的署名是什么？请按原文回答，不把贡献分组成员当作全局作者排名。",
            ["BigScience Workshop"],
            [1],
            None,
        ),
        (
            "major-first-three",
            "只根据首页 Major Contributors 分组，列出该分组开头的三位成员；"
            "不要称为整篇论文第一、第二、第三作者。",
            ["Teven Le Scao", "Angela Fan", "Christopher Akiki"],
            [1],
            "Major Contributors",
        ),
        (
            "prompt-last-member",
            "BLOOM 前置信息中，Prompt Engineering 跨页名单最后一位是谁？请引用名单结束位置。",
            ["Hadar Tojarieh"],
            [2],
            "Prompt Engineering",
        ),
        (
            "application-cross-page-name",
            "Applications 分组在物理第2页末尾和第3页开头拆开的姓名应如何连起来？"
            "请同时引用这两页，不将半个姓名当完整姓名。",
            ["Carlos Muñoz Ferrandis"],
            [2, 3],
            "Applications",
        ),
        (
            "organization-member",
            "Thomas Wolf 是否出现在前置信息的 Organization 贡献分组？给出该分组原文位置。",
            ["Thomas Wolf"],
            [3],
            "Organization",
        ),
        (
            "unsupported-affiliations",
            "仅依据 BLOOM 前三页前置信息，能否确定 Major Contributors 每一位成员分别所属哪所大学？"
            "若没有逐人机构对应表请明确说明，不要推测。",
            [],
            [],
            None,
        ),
        (
            "two-role-members",
            "请仅核对 Angela Fan 和 Thomas Wolf 是否都同时出现在 Major Contributors "
            "与 Organization "
            "两个分组，分别引用两个分组，不额外列出其他成员。",
            ["Angela Fan", "Thomas Wolf"],
            [1, 3],
            None,
        ),
        (
            "wrapped-author-name",
            "在首页 Major Contributors 分组中，Alexander M. 后面紧接的姓氏是什么？"
            "请给出完整姓名，不把换行后的 Rush 漏掉。",
            ["Alexander M. Rush"],
            [1],
            "Major Contributors",
        ),
    ]
    report = {
        "status": "running",
        "runtime_source": inspect_source(project / "backend"),
        "source_sha256": digest(source),
        "ast_sha256": digest(ast_path),
        "chunks": count,
        "document_id": doc_id,
        "provider": provider.name,
        "model": provider.model,
        "cloud_calls": 0,
        "automatic_retries": 0,
        "cases": [],
        "production_library_modified": False,
        "scientific_review": "pending",
        "scope": "Eight public BLOOM front-matter questions; "
        "real new BGE index, TEI and DeepSeek via ASGI",
        "embedding_calls": [],
        "index_reused_readonly": bool(reuse),
    }
    calls = []

    def save():
        report["cloud_calls"] = len(calls)
        write_json(root / "report.json", report)
        write_json(root / "model-calls.json", calls)

    embedder = get_embedding_provider()
    original_embed = embedder.embed_dense

    def recorded_embed(texts):
        item = {"input_count": len(texts), "status": "started"}
        report["embedding_calls"].append(item)
        save()
        started = time.monotonic()
        try:
            vectors = original_embed(texts)
            item.update(status="returned", dimensions=[len(v) for v in vectors])
            return vectors
        except Exception as exc:
            item.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            item["elapsed_seconds"] = round(time.monotonic() - started, 3)
            save()

    embedder.embed_dense = recorded_embed
    index = QdrantVectorIndex(settings, embedder=embedder)
    report["temporary_vector_collection"] = index.collection_name
    original_post = provider._post_response

    async def recorded_post(payload):
        item = {"status": "started", "request": payload}
        calls.append(item)
        save()
        try:
            response = await original_post(payload)
            item.update(status="returned", response=response)
            return response
        except Exception as exc:
            item.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            save()

    provider._post_response = recorded_post
    try:
        with SessionLocal() as db:
            if not reuse:
                assert index.index_document(db, doc_id) == count
            else:
                rows = db.scalars(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                ).all()
                expected_points = {
                    row.id: (
                        row.text,
                        row.page_number,
                        json.loads(row.block_ids_json),
                        json.loads(row.bbox_json),
                        row.section,
                    )
                    for row in rows
                }
                points, offset = [], None
                while True:
                    batch, offset = index.client.scroll(
                        collection_name=index.collection_name,
                        limit=128,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    points.extend(batch)
                    if offset is None:
                        break
                actual_points = {
                    str(point.id): (
                        point.payload["text"],
                        point.payload["page_number"],
                        point.payload["block_ids"],
                        point.payload["bbox"],
                        point.payload["section"],
                    )
                    for point in points
                }
                assert actual_points == expected_points, "Every reused point must match SQL/AST"
                actual_drafts = Counter(
                    (row.page_number, row.section, row.text, row.block_ids_json, row.bbox_json)
                    for row in rows
                )
                expected_drafts = Counter(
                    (
                        draft.page_number,
                        draft.section,
                        draft.text,
                        json.dumps(draft.block_ids, ensure_ascii=False),
                        json.dumps(draft.bbox),
                    )
                    for draft in build_chunk_drafts(ast)
                )
                assert actual_drafts == expected_drafts
                report["all_reused_payloads_verified"] = len(points)
            assert index.document_point_count(doc_id) == count
        drafts = build_chunk_drafts(ast)
        with (
            patch("app.services.vector_index.get_vector_index", return_value=index),
            patch("app.agent.harness.get_llm_provider", return_value=provider),
            TestClient(app) as client,
        ):
            for case_id, question, facts, pages, role in tasks:
                before_calls = len(calls)
                response = client.post(
                    "/api/v1/agent/ask",
                    json={"question": question, "document_ids": [doc_id], "top_k": 16},
                )
                row = {
                    "case_id": case_id,
                    "question": question,
                    "http_status": response.status_code,
                    "independent_expected_names": facts,
                    "required_physical_pages": pages,
                    "required_role": role,
                    "checks": {},
                    "model_calls": len(calls) - before_calls,
                }
                report["cases"].append(row)
                if response.status_code == 200:
                    answer = AskResponse.model_validate(response.json())
                    row["response"] = answer.model_dump(mode="json")
                    evidence = {e.evidence_id: e for e in answer.evidence}
                    cited = {key for claim in answer.claims for key in claim.evidence_ids}
                    anchors = [evidence[key] for key in cited if key in evidence]
                    checked = row["checks"]
                    checked["real_deepseek_not_fallback"] = answer.provider == "deepseek"
                    checked["all_evidence_actual_scoped_source"] = bool(evidence) and all(
                        e.document_id == doc_id and source_anchor_valid(e, ast, drafts)
                        for e in evidence.values()
                    )
                    checked["actual_hybrid_reranked"] = bool(evidence) and all(
                        e.recall_mode == "hybrid" and e.retrieval_mode == "reranked"
                        for e in evidence.values()
                    )
                    if case_id == "unsupported-affiliations":
                        checked["honest_missing_affiliation_answer"] = bool(
                            re.search(r"无法|不能|未提供|未列出|没有|不足|未.*对应", answer.answer)
                        )
                        checked["no_invented_university"] = not re.search(
                            r"Stanford|Harvard|MIT|Oxford|清华|北京大学", answer.answer, re.I
                        )
                    else:
                        checked["complete_not_refusal"] = (
                            bool(answer.claims) and not answer.insufficient_evidence
                        )
                        checked["valid_claim_citations"] = bool(cited) and cited <= set(evidence)
                        checked["expected_names_in_claims"] = all(
                            any(plain(fact) in plain(claim.text) for claim in answer.claims)
                            for fact in facts
                        )
                        checked["required_pages_cited"] = set(pages) <= {
                            e.page_number for e in anchors
                        }
                        checked["role_source_cited"] = not role or any(
                            e.section == "贡献分组 · " + role for e in anchors
                        )
                        if case_id == "wrapped-author-name":
                            assignments = re.findall(
                                r"(?:完整姓名|全名)\s*(?:为|是|[:：])\s*"
                                r"([A-Za-z. ]+)",
                                answer.answer,
                            )
                            checked["full_name_assignment_not_next_person"] = bool(
                                assignments
                            ) and all(
                                plain(value) == plain("Alexander M. Rush") for value in assignments
                            )
                    row["status"] = (
                        "machine_passed_pending_source_review"
                        if all(checked.values())
                        else "failed"
                    )
                else:
                    row.update(status="failed", response=response.text[:2000])
                save()
                print(f"{case_id}: {row['status']}", flush=True)
        report["status"] = "machine_checks_complete_pending_source_review"
    except Exception as exc:
        report.update(
            status="failed",
            error={
                "type": type(exc).__name__,
                "message": re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc))[:1000],
            },
        )
    finally:
        report["source_unchanged"] = (
            digest(source) == report["source_sha256"] and digest(ast_path) == expected
        )
        save()
    print(
        json.dumps(
            {
                "status": report["status"],
                "executed": len(report["cases"]),
                "machine_passed": sum(
                    c["status"] == "machine_passed_pending_source_review" for c in report["cases"]
                ),
                "cloud_calls": report["cloud_calls"],
                "error": report.get("error"),
            }
        ),
        flush=True,
    )
    return (
        0
        if len(report["cases"]) == 8
        and all(c["status"] == "machine_passed_pending_source_review" for c in report["cases"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
