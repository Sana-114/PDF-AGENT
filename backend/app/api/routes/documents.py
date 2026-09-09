import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document, DocumentStatus
from app.parsers.checkpoint import checkpoint_progress
from app.schemas.document import (
    DocumentList,
    DocumentOutlineRead,
    DocumentProgressRead,
    DocumentRead,
    DocumentReferencesRead,
    UploadResult,
)
from app.services.document_content import (
    outline_items,
    read_parsed_document,
    reference_links,
)
from app.services.storage import storage
from app.services.vector_index import delete_document_index_safely
from app.workers.tasks import parse_document

router = APIRouter()


@router.post("", response_model=UploadResult, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
) -> UploadResult:
    staged = await storage.stage_pdf(file)
    existing = db.scalar(select(Document).where(Document.sha256 == staged.sha256))
    if existing:
        staged.discard()
        return UploadResult(
            document=DocumentRead.model_validate(existing),
            exact_duplicate=True,
            message="检测到内容完全相同的 PDF，未重复入库。",
        )

    document = Document(
        original_filename=file.filename or "document.pdf",
        storage_key=staged.storage_key,
        content_type=file.content_type or "application/pdf",
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
        return UploadResult(
            document=DocumentRead.model_validate(existing),
            exact_duplicate=True,
            message="检测到并发上传的相同 PDF，未重复入库。",
        )

    try:
        parse_document.delay(document.id)
        db.refresh(document)
    except Exception as exc:
        document.status = DocumentStatus.FAILED
        document.error_message = f"任务投递失败：{exc}"
        db.commit()

    return UploadResult(
        document=DocumentRead.model_validate(document),
        message="PDF 已入库，解析任务已创建。",
    )


@router.get("", response_model=DocumentList)
def list_documents(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentList:
    total = db.scalar(select(func.count()).select_from(Document)) or 0
    items = db.scalars(
        select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return DocumentList(
        items=[DocumentRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


def _get_document(document_id: str, db: Session) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文献不存在。")
    return document


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> DocumentRead:
    return DocumentRead.model_validate(_get_document(document_id, db))


@router.get("/{document_id}/progress", response_model=DocumentProgressRead)
def get_document_progress(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> DocumentProgressRead:
    document = _get_document(document_id, db)
    if document.status == DocumentStatus.READY:
        page_count = document.page_count or 0
        return DocumentProgressRead(
            document_id=document.id,
            status=document.status,
            completed_pages=page_count,
            page_count=document.page_count,
            percentage=100.0,
            resumable=False,
        )

    progress = checkpoint_progress(storage.checkpoint_dir(document.id)) or {}
    return DocumentProgressRead(
        document_id=document.id,
        status=document.status,
        completed_pages=int(progress.get("completed_pages", 0)),
        page_count=progress.get("page_count") or document.page_count,
        percentage=float(progress.get("percentage", 0.0)),
        resumable=bool(progress.get("resumable", False)),
        updated_at=progress.get("updated_at"),
    )


@router.get("/{document_id}/file")
def get_document_file(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> FileResponse:
    document = _get_document(document_id, db)
    path = storage.document_path(document.storage_key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")
    return FileResponse(path, media_type="application/pdf", filename=document.original_filename)


@router.get("/{document_id}/content")
def get_parsed_content(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> dict:
    document = _get_document(document_id, db)
    if document.status != DocumentStatus.READY:
        raise HTTPException(status_code=409, detail="文献尚未解析完成。")
    path = storage.parsed_path(document_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="结构化解析结果不存在。")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{document_id}/outline", response_model=DocumentOutlineRead)
def get_document_outline(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> DocumentOutlineRead:
    document = _get_document(document_id, db)
    if document.status != DocumentStatus.READY:
        raise HTTPException(status_code=409, detail="文献尚未解析完成。")
    parsed = read_parsed_document(document_id)
    if parsed is None:
        raise HTTPException(status_code=404, detail="结构化解析结果不存在。")
    return DocumentOutlineRead(document_id=document_id, items=outline_items(parsed))


@router.get("/{document_id}/references", response_model=DocumentReferencesRead)
def get_document_references(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> DocumentReferencesRead:
    document = _get_document(document_id, db)
    if document.status != DocumentStatus.READY:
        raise HTTPException(status_code=409, detail="文献尚未解析完成。")
    parsed = read_parsed_document(document_id)
    if parsed is None:
        raise HTTPException(status_code=404, detail="结构化解析结果不存在。")
    references, mentions = reference_links(parsed)
    return DocumentReferencesRead(
        document_id=document_id,
        items=references,
        mentions=mentions,
    )


@router.post("/{document_id}/reparse", response_model=DocumentRead, status_code=202)
def reparse_document(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> DocumentRead:
    document = _get_document(document_id, db)
    document.status = DocumentStatus.QUEUED
    document.error_message = None
    db.commit()
    parse_document.delay(document.id)
    db.refresh(document)
    return DocumentRead.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str, db: Annotated[Session, Depends(get_db)]
) -> None:
    document = _get_document(document_id, db)
    delete_document_index_safely(document.id)
    storage.delete(document.storage_key, document.id)
    db.delete(document)
    db.commit()
