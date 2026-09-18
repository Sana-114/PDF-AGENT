"""Explicitly rebuild ready-document vectors for the configured embedding model."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.services.vector_index import get_vector_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="Reindex only this document; repeat for multiple documents.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    init_db()
    index = get_vector_index()
    if not index.enabled:
        raise SystemExit("VECTOR_SEARCH_ENABLED=false; vector reindexing is disabled.")

    started = time.perf_counter()
    results: list[dict] = []
    with SessionLocal() as session:
        query = (
            select(Document)
            .where(Document.status == DocumentStatus.READY)
            .order_by(Document.created_at, Document.id)
        )
        if args.document_ids:
            query = query.where(Document.id.in_(args.document_ids))
        documents = session.scalars(query).all()
        found_ids = {document.id for document in documents}
        missing_ids = sorted(set(args.document_ids or []) - found_ids)

        for document in documents:
            expected = int(
                session.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(DocumentChunk.document_id == document.id)
                )
                or 0
            )
            item_started = time.perf_counter()
            indexed = index.index_document(session, document.id)
            stored = index.document_point_count(document.id)
            results.append(
                {
                    "document_id": document.id,
                    "filename": document.original_filename,
                    "expected_chunks": expected,
                    "indexed_chunks": indexed,
                    "stored_points": stored,
                    "status": "passed" if expected > 0 and stored == expected else "failed",
                    "elapsed_ms": round((time.perf_counter() - item_started) * 1000),
                }
            )

    failures = [item for item in results if item["status"] != "passed"]
    report = {
        "status": "passed" if results and not failures and not missing_ids else "failed",
        "embedding_provider": index.embedder.name,
        "embedding_model": index.embedder.model,
        "embedding_dimensions": index.embedder.dimensions,
        "collection": index.collection_name,
        "documents": results,
        "missing_document_ids": missing_ids,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.strict and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
