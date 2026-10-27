"""Read-only BGE context recall check using an existing isolated official test index.

Restore scoped payloads into temporary SQLite. Never call an LLM, create an
index, mutate the production library, or add a hard-coded context answer.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from qdrant_client import models
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.services.evidence_fields import context_statement  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from scripts.evaluate_official_first_round import ReadOnlyOfficialIndex  # noqa: E402
from scripts.evaluate_real_pdf_comparison import _check_fact  # noqa: E402


def check(report_path: Path, dataset_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Read-only check must not overwrite a report")
    report_bytes, dataset_bytes = report_path.read_bytes(), dataset_path.read_bytes()
    original, dataset = json.loads(report_bytes), json.loads(dataset_bytes)
    if hashlib.sha256(dataset_bytes).hexdigest() != original["dataset_sha256"]:
        raise ValueError("Original dataset SHA differs")
    case = next(item for item in dataset["cases"] if item["case_id"] == "context-evolution")
    documents = [item for item in original["documents"] if item["filename"] in case["documents"]]
    if len(documents) != len(case["documents"]):
        raise ValueError("Required public documents missing")
    settings.vector_collection = original["temporary_vector_collection"]
    index = ReadOnlyOfficialIndex()
    if not index.enabled or index.collection_name != original["temporary_vector_collection_actual"]:
        raise ValueError("Index namespace mismatch; never create or substitute a collection")
    if not index.client.collection_exists(index.collection_name):
        raise ValueError("Saved test collection is absent; do not create it")
    # Search must never recreate this diagnostic collection, even if another
    # process removes it after the existence check. A 404 must remain a failure.
    index._collection_ready = True
    result = {"evaluation_kind": "readonly_official_bge_context_recall", "model_calls": 0,
              "dataset_sha256": original["dataset_sha256"],
              "original_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
              "collection": index.collection_name, "question": case["question"],
              "production_library_modified": False, "vector_index_modified": False,
              "documents": [], "evidence": []}
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with tempfile.TemporaryDirectory(prefix="official-context-readonly-") as parsed_dir:
        settings.parsed_dir = Path(parsed_dir)
        with Session(engine) as session:
            for item in documents:
                session.add(Document(id=item["id"], original_filename=item["filename"],
                                     storage_key=item["filename"], size_bytes=1,
                                     sha256=item["sha256"], title=item["title"],
                                     page_count=item["pages"], status=DocumentStatus.READY))
                session.flush()
                offset, count = None, 0
                while True:
                    points, offset = index.client.scroll(
                        collection_name=index.collection_name,
                        scroll_filter=models.Filter(must=[models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=item["id"])
                        )]), limit=128, offset=offset, with_payload=True, with_vectors=False,
                    )
                    for point in points:
                        payload = point.payload
                        if not payload or payload["document_id"] != item["id"]:
                            raise ValueError("Unexpected payload outside declared public scope")
                        session.add(DocumentChunk(
                            id=payload["chunk_id"], document_id=item["id"], chunk_index=count,
                            page_number=payload["page_number"],
                            block_ids_json=json.dumps(payload["block_ids"]),
                            bbox_json=json.dumps(payload["bbox"]) if payload.get("bbox") else None,
                            section=payload.get("section"), text=payload["text"],
                            char_count=len(payload["text"]),
                        ))
                        count += 1
                    if offset is None:
                        break
                if count != item["chunks"]:
                    raise ValueError("Saved index chunk count mismatch")
                session.commit()
                retriever = HybridRetriever(session, vector_index=index)
                # Matches ask(top_k=18): rank-20 per-document candidates, then six per paper.
                selected = retriever.search(case["question"], [item["id"]], 20)[:6]
                if any(anchor.retrieval_mode != "reranked" or anchor.recall_mode != "hybrid"
                       for anchor in selected):
                    raise ValueError("Configured BGE recall or reranking degraded")
                result["documents"].append({"filename": item["filename"], "id": item["id"],
                                             "chunks": count, "selected": len(selected)})
                for anchor in selected:
                    result["evidence"].append(anchor.model_copy(update={
                        "evidence_id": f"E{len(result['evidence']) + 1}"
                    }).model_dump())
    from types import SimpleNamespace

    from app.schemas.agent import AnswerClaim, EvidenceAnchor

    evidence = [EvidenceAnchor.model_validate(item) for item in result["evidence"]]
    # This is an excerpt-presence probe, NOT a fabricated/published model answer.
    usable = [anchor for anchor in evidence if anchor.score >= settings.rag_min_score]
    result["actual_usable_evidence"] = [anchor.model_dump(mode="json") for anchor in usable]
    probe = SimpleNamespace(evidence=usable, claims=[
        AnswerClaim(text=anchor.quote, evidence_ids=[anchor.evidence_id]) for anchor in usable
    ])
    ids = {item["filename"]: item["id"] for item in documents}
    result["source_fact_recall"] = {fact["fact_id"]: _check_fact(
        fact, probe, ids
    ) for fact in case["facts"]}
    definitions = [anchor for anchor in usable if context_statement(anchor)]
    definition_probe = SimpleNamespace(evidence=definitions, claims=[
        AnswerClaim(text=anchor.quote, evidence_ids=[anchor.evidence_id]) for anchor in definitions
    ])
    result["definition_fact_recall"] = {fact["fact_id"]: _check_fact(
        fact, definition_probe, ids
    ) for fact in case["facts"]}
    result["status"] = "passed" if all(result["definition_fact_recall"].values()) else "failed"
    result["is_new_end_to_end_generation"] = False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.report, args.dataset, args.output)
    print(json.dumps({"status": result["status"], "model_calls": 0,
                      "definition_fact_recall": result["definition_fact_recall"]}))
    if result["status"] != "passed":
        raise SystemExit(1)
