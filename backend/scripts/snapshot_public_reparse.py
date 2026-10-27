"""Back up/audit one explicitly SHA-bound public document before/after reparse.

Read-only with respect to database and Qdrant: never parse, index, delete, or
call an LLM. Write snapshots only under DATA_DIR/acceptance-backups. A database
dump should also be retained by the operator before requesting a reparse.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qdrant_client import models
from sqlalchemy import inspect, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.chunk import DocumentChunk
from app.models.conversation import AgentConversationMessage
from app.models.document import Document, DocumentStatus
from app.services.storage import storage
from app.services.vector_index import QdrantVectorIndex


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def row_data(row) -> dict:
    result = {}
    for column in inspect(type(row)).columns:
        value = getattr(row, column.key)
        result[column.key] = value.isoformat() if isinstance(value, datetime) else value
    return result


def identity_digest(rows: list[dict]) -> str:
    serialized = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(serialized).hexdigest()


def validate_output(value: Path, data_root: Path) -> Path:
    root = (data_root.resolve() / "acceptance-backups").resolve()
    output = value.resolve()
    if not output.is_relative_to(root) or output == root or output.exists():
        raise ValueError("Requires a new dedicated DATA_DIR/acceptance-backups subdirectory")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    UUID(args.document_id)
    if not re.fullmatch(r"[a-f0-9]{64}", args.expected_sha256):
        raise ValueError("Requires an explicit lowercase SHA-256 public source binding")
    output = validate_output(args.output, settings.data_dir)
    with SessionLocal() as db:
        document = db.get(Document, args.document_id)
        if document is None or document.status != DocumentStatus.READY:
            raise ValueError("Selected document must exist and be ready")
        source = storage.document_path(document.storage_key)
        ast = storage.parsed_path(document.id)
        if document.sha256 != args.expected_sha256 or digest(source) != args.expected_sha256:
            raise ValueError("Selected PDF is not the explicitly bound public source")
        chunks = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        others = db.scalars(
            select(Document).where(Document.id != document.id).order_by(Document.id)
        ).all()
        messages = db.scalars(
            select(AgentConversationMessage).order_by(AgentConversationMessage.id)
        ).all()
        snapshot = {
            "document": row_data(document),
            "chunks": [row_data(row) for row in chunks],
        }
        report = {
            "status": "snapshot_ready",
            "selected_document_id": document.id,
            "source_sha256": args.expected_sha256,
            "ast_sha256": digest(ast),
            "parser_version": document.parser_version,
            "chunk_count": len(chunks),
            "other_document_count": len(others),
            "other_document_rows_sha256": identity_digest([row_data(row) for row in others]),
            "existing_message_count": len(messages),
            "existing_message_rows_sha256": identity_digest([row_data(row) for row in messages]),
            "cloud_calls": 0,
            "database_modified": False,
            "vector_index_modified": False,
        }
        index = QdrantVectorIndex(settings.model_copy(update={"vector_timeout_seconds": 30}))
        try:
            if not index.enabled or not index.client.collection_exists(index.collection_name):
                raise ValueError("Existing BGE namespace required; never create one in snapshot")
            records, offset = [], None
            while True:
                points, offset = index.client.scroll(
                    collection_name=index.collection_name,
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id", match=models.MatchValue(value=document.id)
                            )
                        ]
                    ),
                    limit=128,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                if any(point.payload.get("document_id") != document.id for point in points):
                    raise ValueError("Vector export escaped explicit document scope")
                records.extend(point.model_dump(mode="json") for point in points)
                if offset is None:
                    break
                if len(records) > 10000:
                    raise ValueError("Unexpectedly large single-document vector export")
            report["vector_collection"] = index.collection_name
            report["vector_point_count"] = len(records)
            report["point_ids_match_sql_chunks"] = {p["id"] for p in records} == {
                row.id for row in chunks
            }
        finally:
            index.client.close()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source, output / "source.pdf")
    shutil.copyfile(ast, output / "parsed.json")
    for filename, payload in [
        ("document-and-chunks.json", snapshot),
        ("vector-points.json", records),
        ("report.json", report),
    ]:
        (output / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
