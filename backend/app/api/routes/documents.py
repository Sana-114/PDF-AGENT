import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document, DocumentStatus
from app.schemas.document import DocumentList, DocumentRead, UploadResult
from app.services.storage import storage
from app.services.vector_index import delete_document_index_safely
from app.workers.tasks import parse_document

router = APIRouter()


@router.post("", response_model=UploadResult, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
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
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentRead:
    return DocumentRead.model_validate(_get_document(document_id, db))


@router.get("/{document_id}/file")
def get_document_file(document_id: str, db: Session = Depends(get_db)) -> FileResponse:
    document = _get_document(document_id, db)
    path = storage.document_path(document.storage_key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")
    return FileResponse(path, media_type="application/pdf", filename=document.original_filename)


@router.get("/{document_id}/content")
def get_parsed_content(document_id: str, db: Session = Depends(get_db)) -> dict:
    document = _get_document(document_id, db)
    if document.status != DocumentStatus.READY:
        raise HTTPException(status_code=409, detail="文献尚未解析完成。")
    path = storage.parsed_path(document_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="结构化解析结果不存在。")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/{document_id}/reparse", response_model=DocumentRead, status_code=202)
def reparse_document(document_id: str, db: Session = Depends(get_db)) -> DocumentRead:
    document = _get_document(document_id, db)
    document.status = DocumentStatus.QUEUED
    document.error_message = None
    db.commit()
    parse_document.delay(document.id)
    db.refresh(document)
    return DocumentRead.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str, db: Session = Depends(get_db)) -> None:
    document = _get_document(document_id, db)
    delete_document_index_safely(document.id)
    storage.delete(document.storage_key, document.id)
    db.delete(document)
    db.commit()
