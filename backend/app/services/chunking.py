import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.chunk import DocumentChunk
from app.models.document import Document, DocumentStatus
from app.services.storage import storage


@dataclass(slots=True)
class ChunkDraft:
    chunk_index: int
    page_number: int
    block_ids: list[str]
    bbox: list[float] | None
    section: str | None
    text: str


def build_chunk_drafts(parsed: dict[str, Any], target_chars: int = 1200) -> list[ChunkDraft]:
    """Create page-local chunks so every answer can jump back to one PDF page."""

    drafts: list[ChunkDraft] = []
    current_section: str | None = None
    chunk_index = 0

    def flush(
        page_number: int,
        section: str | None,
        texts: list[str],
        block_ids: list[str],
        boxes: list[list[float]],
    ) -> None:
        nonlocal chunk_index
        text = "\n".join(texts).strip()
        if not text:
            return
        drafts.append(
            ChunkDraft(
                chunk_index=chunk_index,
                page_number=page_number,
                block_ids=list(block_ids),
                bbox=_union_bbox(boxes),
                section=section,
                text=text,
            )
        )
        chunk_index += 1
        texts.clear()
        block_ids.clear()
        boxes.clear()

    for page in parsed.get("pages", []):
        page_number = int(page.get("page_number", 1))
        texts: list[str] = []
        block_ids: list[str] = []
        boxes: list[list[float]] = []

        for block in page.get("blocks", []):
            block_text = str(block.get("text", "")).strip()
            if not block_text:
                continue
            if block.get("type") == "heading":
                flush(page_number, current_section, texts, block_ids, boxes)
                current_section = block_text[:500]
            if texts and sum(len(value) for value in texts) + len(block_text) > target_chars:
                flush(page_number, current_section, texts, block_ids, boxes)
            texts.append(block_text)
            block_ids.append(str(block.get("block_id", "")))
            bbox = block.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                boxes.append([float(value) for value in bbox])
        flush(page_number, current_section, texts, block_ids, boxes)

    return drafts


def replace_document_chunks(session: Session, document_id: str, parsed: dict[str, Any]) -> int:
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    drafts = build_chunk_drafts(parsed)
    session.add_all(
        DocumentChunk(
            document_id=document_id,
            chunk_index=draft.chunk_index,
            page_number=draft.page_number,
            block_ids_json=json.dumps(draft.block_ids, ensure_ascii=False),
            bbox_json=json.dumps(draft.bbox) if draft.bbox else None,
            section=draft.section,
            text=draft.text,
            char_count=len(draft.text),
        )
        for draft in drafts
    )
    return len(drafts)


def ensure_document_chunks(session: Session, document_ids: list[str] | None = None) -> int:
    """Backfill chunks for documents parsed before the chunk index was introduced."""

    query = select(Document).where(Document.status == DocumentStatus.READY)
    if document_ids:
        query = query.where(Document.id.in_(document_ids))
    created = 0
    for document in session.scalars(query).all():
        count = session.scalar(
            select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id == document.id
            )
        )
        if count:
            continue
        path = storage.parsed_path(document.id)
        if not path.exists():
            continue
        parsed = json.loads(path.read_text(encoding="utf-8"))
        created += replace_document_chunks(session, document.id, parsed)
    if created:
        session.commit()
    return created


def _union_bbox(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        round(min(box[0] for box in boxes), 2),
        round(min(box[1] for box in boxes), 2),
        round(max(box[2] for box in boxes), 2),
        round(max(box[3] for box in boxes), 2),
    ]
