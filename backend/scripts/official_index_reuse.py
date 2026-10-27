"""Verify and restore an already audited isolated index without vector writes."""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from official_functional_inputs import TEMP_ROOT, digest


def saved_index_metadata(source: Path, rows: list[dict]) -> dict:
    source = source.resolve()
    if source == TEMP_ROOT or not source.is_relative_to(TEMP_ROOT):
        raise ValueError("Only isolated acceptance indexes may be reused")
    previous_rows = json.loads((source / "fixture.json").read_bytes())
    metadata = json.loads((source / "server-source.json").read_bytes())
    if (previous_rows != rows or metadata.get("production_library_modified") is not False
            or not metadata.get("allow_local_bge")
            or metadata.get("source_fixture_sha256") != digest(source / "fixture.json")):
        raise ValueError("Saved index source scope/provenance differs")
    namespace = metadata.get("vector_collection", "")
    if (not isinstance(namespace, str)
            or not re.fullmatch(
                r"official_runtime_[a-f0-9]{32}_semantic_[a-f0-9]{10}_1024", namespace
            )
            or type(metadata.get("indexed_chunks")) is not int or metadata["indexed_chunks"] < 1):
        raise ValueError("Never reuse a production or arbitrary vector namespace")
    for row in rows:
        for folder, suffix, field in (("uploads", ".pdf", "source_sha256"),
                                       ("parsed", ".json", "ast_sha256")):
            path = (source / folder / (row["document_id"] + suffix)).resolve()
            if not path.is_relative_to(source) or digest(path) != row[field]:
                raise ValueError("Saved PDF/AST bytes changed")
    return metadata


def _signature(page, section, text, ids, bbox):
    return page, section, text, tuple(ids), tuple(bbox or [])


def restore_verified_index(session, index, root: Path, rows: list[dict], metadata: dict) -> int:
    """Check every payload against current AST before inserting into EMPTY local SQL.

    No embedding call, upsert, collection creation/deletion or source-SQL write.
    The caller must use ReadOnlyOfficialIndex throughout the serving process.
    """
    from sqlalchemy import func, select

    from app.models.chunk import DocumentChunk
    from app.models.document import Document
    from app.services.chunking import build_chunk_drafts
    from app.services.text_features import infer_source_type

    if index.collection_name != metadata["vector_collection"]:
        raise ValueError("Embedding namespace/dimensions differ from saved index")
    if not index.client.collection_exists(index.collection_name):
        raise ValueError("Saved index absent; never recreate it")
    expected_ids = {row["document_id"] for row in rows}
    existing = session.scalar(select(func.count()).select_from(DocumentChunk))
    if len(expected_ids) != len(rows) or existing:
        raise ValueError("Reuse requires an empty isolated SQL chunk store and unique sources")
    if {doc.id for doc in session.scalars(select(Document)).all()} != expected_ids:
        raise ValueError("Isolated SQL document scope differs")
    points, offset, seen_offsets = [], None, set()
    while True:
        batch, next_offset = index.client.scroll(
            collection_name=index.collection_name, limit=128, offset=offset,
            with_payload=True, with_vectors=False,
        )
        points.extend(batch)
        if len(points) > metadata["indexed_chunks"]:
            raise ValueError("Saved collection has unexpected extra points")
        if next_offset is None:
            break
        if str(next_offset) in seen_offsets:
            raise ValueError("Saved index pagination repeated; refuse partial restore")
        seen_offsets.add(str(next_offset))
        offset = next_offset
    if len(points) != metadata["indexed_chunks"]:
        raise ValueError("Saved vector count differs; no automatic backfill")
    by_document, ids = defaultdict(list), set()
    for point in points:
        p = point.payload or {}
        chunk_id = p.get("chunk_id")
        if (p.get("document_id") not in expected_ids or not chunk_id
                or str(point.id) != chunk_id or chunk_id in ids):
            raise ValueError("Saved point identity/scope invalid")
        ids.add(chunk_id)
        by_document[p["document_id"]].append(p)
    pending = []
    for row in rows:
        doc_id = row["document_id"]
        path = root / "parsed" / (doc_id + ".json")
        if digest(path) != row["ast_sha256"]:
            raise ValueError("Current isolated AST changed")
        drafts = build_chunk_drafts(json.loads(path.read_bytes()))
        expected = Counter(_signature(d.page_number, d.section, d.text, d.block_ids, d.bbox)
                           for d in drafts)
        actual, lookup = Counter(), defaultdict(list)
        for p in by_document[doc_id]:
            signature = _signature(p["page_number"], p.get("section"), p["text"],
                                   p["block_ids"], p.get("bbox"))
            actual[signature] += 1
            lookup[signature].append(p)
        if actual != expected:
            raise ValueError("Saved payloads differ from complete AST; never repair in place")
        document = session.get(Document, doc_id)
        if (document.sha256 != row["source_sha256"]
                or document.original_filename != row["filename"]):
            raise ValueError("Local SQL source identity differs")
        for draft in drafts:
            signature = _signature(draft.page_number, draft.section, draft.text,
                                   draft.block_ids, draft.bbox)
            p = lookup[signature].pop()
            if p.get("document_title") != document.title:
                raise ValueError("Saved title attribution differs")
            if p.get("source_type") != infer_source_type(draft.section, draft.block_ids):
                raise ValueError("Saved body/reference source type differs")
            pending.append(DocumentChunk(
                id=p["chunk_id"], document_id=doc_id, chunk_index=draft.chunk_index,
                page_number=draft.page_number, section=draft.section, text=draft.text,
                block_ids_json=json.dumps(draft.block_ids),
                bbox_json=json.dumps(draft.bbox) if draft.bbox else None,
                char_count=len(draft.text),
            ))
    session.add_all(pending)
    session.commit()
    return len(pending)
