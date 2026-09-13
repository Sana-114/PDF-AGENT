import json
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMResponseError
from app.models.document import Document, DocumentStatus
from app.models.paper_source import PaperSource
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
from app.services.document_ingestion import dispatch_document_parse, persist_staged_document
from app.services.document_translation import page_translation_payload
from app.services.document_translation_export import build_document_translation_export
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
    result = persist_staged_document(
        db,
        staged,
        original_filename=file.filename or "document.pdf",
        content_type=file.content_type or "application/pdf",
    )
    if not result.exact_duplicate:
        dispatch_document_parse(db, result.document)

    return UploadResult(
        document=DocumentRead.model_validate(result.document),
        exact_duplicate=result.exact_duplicate,
        message=result.message,
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


@router.get("/{document_id}/translations/{target_language}/export")
def export_document_translation(
    document_id: str,
    target_language: Literal["zh", "en"],
    db: Annotated[Session, Depends(get_db)],
    output_format: Annotated[Literal["markdown", "html"], Query(alias="format")] = "markdown",
    mode: Annotated[Literal["translation", "bilingual"], Query()] = "bilingual",
) -> Response:
    document = _get_document(document_id, db)
    manifest = _translation_manifest_for_document(document, target_language)
    if manifest is None:
        raise HTTPException(status_code=404, detail="尚未创建该语言的整篇翻译任务。")
    if manifest.get("status") != "completed":
        raise HTTPException(status_code=409, detail="整篇翻译完成后才能导出。")

    page_count = int(manifest.get("page_count", 0))
    pages: list[dict] = []
    missing_pages: list[int] = []
    for page_number in range(1, page_count + 1):
        payload = translation_store.read_page(
            document_id,
            target_language,
            page_number,
            source_fingerprint=document.sha256,
        )
        if payload is None:
            missing_pages.append(page_number)
        else:
            pages.append(payload)
    if missing_pages:
        preview = ", ".join(str(value) for value in missing_pages[:8])
        suffix = "…" if len(missing_pages) > 8 else ""
        raise HTTPException(
            status_code=409,
            detail=f"翻译缓存不完整，缺少第 {preview}{suffix} 页，请继续翻译任务。",
        )

    artifact = build_document_translation_export(
        title=document.title,
        original_filename=document.original_filename,
        manifest=manifest,
        pages=pages,
        output_format=output_format,
        mode=mode,
    )
    ascii_filename = f"translation-{document.id[:12]}.{artifact.filename.rsplit('.', 1)[-1]}"
    disposition = (
        f'attachment; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{quote(artifact.filename)}"
    )
    return Response(
        content=artifact.content.encode("utf-8"),
        media_type=artifact.media_type,
        headers={"Content-Disposition": disposition},
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
    db.execute(sql_delete(PaperSource).where(PaperSource.document_id == document.id))
    db.delete(document)
    db.commit()
