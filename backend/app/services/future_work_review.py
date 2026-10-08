"""Durable human decisions tied to actual, re-verifiable source passages."""

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus
from app.models.future_work import FutureWorkDecision
from app.models.paper_source import PaperSource
from app.schemas.agent import EvidenceAnchor
from app.schemas.review import FutureWorkDecisionRead, FutureWorkDecisionRequest, FutureWorkSource
from app.services.document_content import read_parsed_document


def direction_key(document_id: str, page_number: int, block_ids: list[str], quote: str) -> str:
    payload = json.dumps(
        [document_id, page_number, block_ids, " ".join(quote.split())], ensure_ascii=False
    )
    return "future-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def verify_passage(db: Session, source: FutureWorkSource) -> tuple[Document, dict[str, Any]]:
    document = db.get(Document, source.document_id)
    if document is None or document.status != DocumentStatus.READY:
        raise ValueError("证据论文不存在或尚未解析完成。")
    parsed = read_parsed_document(document.id)
    if parsed is None:
        raise ValueError("证据论文的解析结果不存在。")
    blocks = {
        str(block.get("block_id")): block
        for page in parsed.get("pages", [])
        if page.get("page_number") == source.page_number
        for block in page.get("blocks", [])
    }
    if any(block_id not in blocks for block_id in source.block_ids):
        raise ValueError("证据页码或块 ID 与原文不符。")
    text = " ".join(" ".join(str(blocks[key].get("text", "")).split()) for key in source.block_ids)
    if " ".join(source.quote.split()) not in text:
        raise ValueError("证据摘录必须是指定原文块中的连续文本。")
    return document, blocks[source.block_ids[0]]


def decision_read(db: Session, row: FutureWorkDecision) -> FutureWorkDecisionRead:
    from app.services.evidence_review import _publication_year

    source_document, _ = verify_passage(db, FutureWorkSource.model_validate_json(row.source_json))
    source_year = _publication_year(source_document, db.get(PaperSource, source_document.id))
    anchors = []
    for index, item in enumerate(json.loads(row.progress_json), 1):
        source = FutureWorkSource.model_validate(item)
        document, block = verify_passage(db, source)
        if row.decision == "confirmed_addressed":
            progress_year = _publication_year(document, db.get(PaperSource, document.id))
            if (
                document.id == source_document.id
                or source_year is None
                or progress_year is None
                or progress_year <= source_year
            ):
                raise ValueError("已保存的后续证据时间线无法复核。")
        anchors.append(
            EvidenceAnchor(
                evidence_id=f"{row.direction_id}-progress-{index}",
                document_id=document.id,
                document_title=document.title or document.original_filename,
                page_number=source.page_number,
                block_ids=source.block_ids,
                bbox=block.get("bbox"),
                quote=source.quote,
                score=1,
            )
        )
    if row.decision == "confirmed_addressed" and not anchors:
        raise ValueError("缺少后续证据。")
    return FutureWorkDecisionRead(
        direction_id=row.direction_id,
        decision=row.decision,
        reason=row.reason,
        progress_evidence=anchors,
        updated_at=row.updated_at,
    )


def save_decision(
    db: Session, key: str, request: FutureWorkDecisionRequest
) -> FutureWorkDecisionRead:
    # Local import avoids a service cycle. No LLM call is needed to validate a decision.
    from app.services.evidence_review import _extract_drafts, _publication_year

    document, _ = verify_passage(db, request.source)
    parsed = read_parsed_document(document.id)
    source_year = _publication_year(document, db.get(PaperSource, document.id))
    drafts = _extract_drafts(document, parsed, source_year)
    if key != direction_key(
        document.id, request.source.page_number, request.source.block_ids, request.source.quote
    ):
        raise ValueError("方向 ID 与来源不一致；请重新整理综述。")
    if not any(
        item.kind == "future_work"
        and direction_key(item.document_id, item.page_number, item.block_ids, item.quote) == key
        for item in drafts
    ):
        raise ValueError("该来源不是系统识别出的未来工作段落。")
    if request.decision == "confirmed_addressed" and not request.progress:
        raise ValueError("人工确认已有解决证据时，必须提供后续论文的原文摘录。")
    for source in request.progress:
        progress_document, _ = verify_passage(db, source)
        if progress_document.id == document.id:
            raise ValueError("后续进展不能引用提出该方向的同一篇论文。")
        progress_year = _publication_year(
            progress_document, db.get(PaperSource, progress_document.id)
        )
        if request.decision == "confirmed_addressed" and (
            source_year is None or progress_year is None or progress_year <= source_year
        ):
            raise ValueError("确认已有解决证据需要可核验的更晚发表年份；未知年份只能标记待核验。")
    row = db.get(FutureWorkDecision, key)
    if row is None:
        row = FutureWorkDecision(direction_id=key, source_document_id=document.id)
        db.add(row)
    row.source_json = request.source.model_dump_json()
    row.decision = request.decision
    row.reason = request.reason
    row.progress_json = json.dumps(
        [item.model_dump() for item in request.progress], ensure_ascii=False
    )
    db.commit()
    db.refresh(row)
    return decision_read(db, row)
