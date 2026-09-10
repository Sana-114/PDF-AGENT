from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus
from app.services.storage import StagedUpload
from app.workers.tasks import parse_document


@dataclass(slots=True)
class IngestionResult:
    document: Document
    exact_duplicate: bool
    message: str


def persist_staged_document(
    db: Session,
    staged: StagedUpload,
    *,
    original_filename: str,
    content_type: str = "application/pdf",
) -> IngestionResult:
    """Commit a staged PDF and preserve the existing exact-deduplication contract."""
    existing = db.scalar(select(Document).where(Document.sha256 == staged.sha256))
    if existing:
        staged.discard()
        return IngestionResult(
            document=existing,
            exact_duplicate=True,
            message="检测到内容完全相同的 PDF，未重复入库。",
        )

    document = Document(
        original_filename=original_filename,
        storage_key=staged.storage_key,
        content_type=content_type,
        size_bytes=staged.size_bytes,
        sha256=staged.sha256,
        status=DocumentStatus.QUEUED,
    )
    try:
        staged.commit()
        db.add(document)
        db.commit()
        db.refresh(document)
    except IntegrityError:
        db.rollback()
        staged.discard()
        existing = db.scalar(select(Document).where(Document.sha256 == staged.sha256))
        if existing is None:
            raise
        return IngestionResult(
            document=existing,
            exact_duplicate=True,
            message="检测到并发入库的相同 PDF，未重复入库。",
        )

    return IngestionResult(
        document=document,
        exact_duplicate=False,
        message="PDF 已入库，解析任务已创建。",
    )


def dispatch_document_parse(db: Session, document: Document) -> None:
    try:
        parse_document.delay(document.id)
        db.refresh(document)
    except Exception as exc:
        document.status = DocumentStatus.FAILED
        document.error_message = f"任务投递失败：{exc}"
        db.commit()
