import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMResponseError
from app.models.document import Document, DocumentStatus
from app.parsers.checkpoint import checkpoint_progress
from app.schemas.document import (
    DocumentList,
    DocumentOutlineRead,
    DocumentPageTranslationRead,
    DocumentProgressRead,
    DocumentRead,
    DocumentReferencesRead,
    PageTranslationRequest,
    TranslationJobRead,
    TranslationJobRequest,
    UploadResult,
)
from app.services.document_content import (
    outline_items,
    page_text_segments,
    read_parsed_document,
    reference_links,
)
from app.services.document_translation import page_translation_payload
from app.services.storage import storage
from app.services.translation_store import translation_store
from app.services.vector_index import delete_document_index_safely
from app.workers.tasks import parse_document, translate_document

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


@router.post(
    "/{document_id}/translations/pages/{page_number}",
    response_model=DocumentPageTranslationRead,
)
async def translate_document_page(
    document_id: str,
    page_number: int,
    request: PageTranslationRequest,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentPageTranslationRead:
    if page_number < 1:
        raise HTTPException(status_code=422, detail="页码必须大于或等于 1。")
    document = _get_document(document_id, db)
    if document.status != DocumentStatus.READY:
        raise HTTPException(status_code=409, detail="文献尚未解析完成。")
    parsed = read_parsed_document(document_id)
    if parsed is None:
        raise HTTPException(status_code=404, detail="结构化解析结果不存在。")
    cached = translation_store.read_page(
        document_id,
        request.target_language,
        page_number,
        source_fingerprint=document.sha256,
    )
    if cached is not None:
        return DocumentPageTranslationRead.model_validate(cached)
    existing_manifest = translation_store.read_manifest(document_id, request.target_language)
    if existing_manifest and existing_manifest.get("source_fingerprint") == document.sha256:
        if existing_manifest.get("status") in {"queued", "processing"}:
            raise HTTPException(status_code=409, detail="整篇翻译正在处理，当前页尚未完成。")
    segments, truncated = page_text_segments(parsed, page_number)
    if not segments:
        raise HTTPException(status_code=404, detail="目标页面没有可翻译的文本块。")
    try:
        provider = get_llm_provider()
        if not provider.supports_translation:
            raise LLMConfigurationError(
                "双语阅读需要生成式 LLM；请在 .env 中配置 LLM_PROVIDER、LLM_MODEL 和 LLM_API_KEY。"
            )
        translated = await provider.translate_segments(
            [(item["block_id"], item["source_text"]) for item in segments],
            "auto",
            request.target_language,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    translation_store.prepare(
        document_id=document_id,
        target_language=request.target_language,
        source_fingerprint=document.sha256,
        page_count=document.page_count or len(parsed.get("pages", [])),
        provider=provider.name,
        model=provider.model,
        activate=False,
    )
    payload = page_translation_payload(
        document_id=document_id,
        page_number=page_number,
        target_language=request.target_language,
        provider=provider.name,
        model=provider.model,
        segments=segments,
        translated=translated.items,
        truncated=truncated,
    )
    translation_store.write_page(document_id, request.target_language, page_number, payload)
    return DocumentPageTranslationRead.model_validate(payload)


def _translation_manifest_for_document(
    document: Document,
    target_language: str,
) -> dict | None:
    manifest = translation_store.read_manifest(document.id, target_language)
    if manifest is None or manifest.get("source_fingerprint") != document.sha256:
        return None
    return manifest


@router.post(
    "/{document_id}/translations",
    response_model=TranslationJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_document_translation(
    document_id: str,
    request: TranslationJobRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TranslationJobRead:
    document = _get_document(document_id, db)
    if document.status != DocumentStatus.READY:
        raise HTTPException(status_code=409, detail="文献尚未解析完成。")
    parsed = read_parsed_document(document_id)
    if parsed is None:
        raise HTTPException(status_code=404, detail="结构化解析结果不存在。")
    existing = _translation_manifest_for_document(document, request.target_language)
    if existing and not request.force and existing.get("status") in {
        "queued",
        "processing",
        "completed",
    }:
        return TranslationJobRead.model_validate(translation_store.public_status(existing))
    try:
        provider = get_llm_provider()
        if not provider.supports_translation:
            raise LLMConfigurationError(
                "整篇翻译需要生成式 LLM；请在 .env 中配置 LLM_PROVIDER、LLM_MODEL 和 LLM_API_KEY。"
            )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    manifest = translation_store.prepare(
        document_id=document_id,
        target_language=request.target_language,
        source_fingerprint=document.sha256,
        page_count=document.page_count or len(parsed.get("pages", [])),
        provider=provider.name,
        model=provider.model,
        activate=True,
        force=request.force,
    )
    try:
        translate_document.delay(document_id, request.target_language, False)
    except Exception as exc:
        manifest = translation_store.set_status(
            document_id,
            request.target_language,
            "failed",
            error=f"任务投递失败：{exc}"[:2000],
        )
    else:
        manifest = translation_store.read_manifest(document_id, request.target_language) or manifest
    return TranslationJobRead.model_validate(translation_store.public_status(manifest))


@router.get(
    "/{document_id}/translations/{target_language}",
    response_model=TranslationJobRead,
)
def get_document_translation_status(
    document_id: str,
    target_language: Literal["zh", "en"],
    db: Annotated[Session, Depends(get_db)],
) -> TranslationJobRead:
    document = _get_document(document_id, db)
    manifest = _translation_manifest_for_document(document, target_language)
    if manifest is None:
        raise HTTPException(status_code=404, detail="尚未创建该语言的整篇翻译任务。")
    return TranslationJobRead.model_validate(translation_store.public_status(manifest))


@router.get(
    "/{document_id}/translations/{target_language}/pages/{page_number}",
    response_model=DocumentPageTranslationRead,
)
def get_cached_document_page_translation(
    document_id: str,
    target_language: Literal["zh", "en"],
    page_number: int,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentPageTranslationRead:
    if page_number < 1:
        raise HTTPException(status_code=422, detail="页码必须大于或等于 1。")
    document = _get_document(document_id, db)
    cached = translation_store.read_page(
        document_id,
        target_language,
        page_number,
        source_fingerprint=document.sha256,
    )
    if cached is None:
        raise HTTPException(status_code=404, detail="该页译文尚未生成。")
    return DocumentPageTranslationRead.model_validate(cached)


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
