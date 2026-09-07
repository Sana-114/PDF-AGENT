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

    _append_structured_drafts(parsed, drafts, chunk_index)
    return drafts


def _append_structured_drafts(
    parsed: dict[str, Any], drafts: list[ChunkDraft], start_index: int
) -> None:
    chunk_index = start_index

    def append(
        *,
        page_number: int,
        node_ids: list[str],
        bbox: list[float] | None,
        section: str,
        text: str,
    ) -> None:
        nonlocal chunk_index
        clean_text = text.strip()
        if not clean_text:
            return
        drafts.append(
            ChunkDraft(
                chunk_index=chunk_index,
                page_number=page_number,
                block_ids=node_ids,
                bbox=bbox,
                section=section,
                text=clean_text,
            )
        )
        chunk_index += 1

    for table in parsed.get("tables", []):
        table_id = str(table.get("table_id", "table"))
        caption = str(table.get("caption") or table_id)
        markdown = str(table.get("markdown") or "")
        node_ids = [table_id]
        if table.get("caption_block_id"):
            node_ids.append(str(table["caption_block_id"]))
        append(
            page_number=int(table.get("page_number", 1)),
            node_ids=node_ids,
            bbox=_valid_bbox(table.get("bbox")),
            section=f"表格 · {caption}",
            text=f"{caption}\n{markdown}",
        )

    for figure in parsed.get("figures", []):
        caption = str(figure.get("caption") or "").strip()
        if not caption:
            continue
        figure_id = str(figure.get("figure_id", "figure"))
        node_ids = [figure_id]
        if figure.get("caption_block_id"):
            node_ids.append(str(figure["caption_block_id"]))
        append(
            page_number=int(figure.get("page_number", 1)),
            node_ids=node_ids,
            bbox=_valid_bbox(figure.get("bbox")),
            section=f"图表 · {caption}",
            text=caption,
        )

    for formula in parsed.get("formulas", []):
        formula_id = str(formula.get("formula_id", "formula"))
        block_id = str(formula.get("block_id", ""))
        append(
            page_number=int(formula.get("page_number", 1)),
            node_ids=[value for value in (formula_id, block_id) if value],
            bbox=_valid_bbox(formula.get("bbox")),
            section=f"公式 · {formula_id}",
            text=str(formula.get("text") or ""),
        )

    for reference in parsed.get("references", []):
        label = str(reference.get("label") or "?")
        reference_id = str(reference.get("reference_id", f"ref-{label}"))
        node_ids = [reference_id, *map(str, reference.get("block_ids", []))]
        append(
            page_number=int(reference.get("page_number", 1)),
            node_ids=list(dict.fromkeys(node_ids)),
            bbox=_valid_bbox(reference.get("bbox")),
            section=f"参考文献 · [{label}]",
            text=f"[{label}] {reference.get('text', '')}",
        )


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
        path = storage.parsed_path(document.id)
        if not path.exists():
            continue
        parsed = json.loads(path.read_text(encoding="utf-8"))
        count = session.scalar(
            select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id == document.id
            )
        )
        if count and not _needs_structured_backfill(session, document.id, parsed):
            continue
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


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return [round(float(item), 2) for item in value]


def _needs_structured_backfill(
    session: Session, document_id: str, parsed: dict[str, Any]
) -> bool:
    expected = _structured_node_ids(parsed)
    if not expected:
        return False
    rows = session.scalars(
        select(DocumentChunk.block_ids_json).where(DocumentChunk.document_id == document_id)
    ).all()
    indexed = {
        str(node_id)
        for value in rows
        for node_id in json.loads(value)
    }
    return not expected.issubset(indexed)


def _structured_node_ids(parsed: dict[str, Any]) -> set[str]:
    node_ids = {
        str(table["table_id"])
        for table in parsed.get("tables", [])
        if table.get("table_id")
    }
    node_ids.update(
        str(figure["figure_id"])
        for figure in parsed.get("figures", [])
        if figure.get("figure_id") and figure.get("caption")
    )
    node_ids.update(
        str(formula["formula_id"])
        for formula in parsed.get("formulas", [])
        if formula.get("formula_id")
    )
    node_ids.update(
        str(reference["reference_id"])
        for reference in parsed.get("references", [])
        if reference.get("reference_id")
    )
    return node_ids
