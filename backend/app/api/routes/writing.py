import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMResponseError
from app.models.document import Document, DocumentStatus
from app.models.translation import AcademicTranslationDraft, TranslationGlossary
from app.schemas.academic_translation import (
    AcademicTranslationRead,
    AcademicTranslationRequest,
)
from app.schemas.translation_workspace import (
    AcademicTranslationDraftCreate,
    AcademicTranslationDraftList,
    AcademicTranslationDraftRead,
    AcademicTranslationDraftUpdate,
    TranslationGlossaryCreate,
    TranslationGlossaryList,
    TranslationGlossaryRead,
)
from app.schemas.writing import WritingOutlineRead, WritingOutlineRequest
from app.services.academic_translation import AcademicTranslationService
from app.services.writing_outline import WritingOutlineService

router = APIRouter()


def _glossary_read(item: TranslationGlossary) -> TranslationGlossaryRead:
    try:
        terms = json.loads(item.terms_json)
    except (TypeError, ValueError):
        terms = []
    return TranslationGlossaryRead(
        id=item.id,
        name=item.name,
        source_language=item.source_language,
        target_language=item.target_language,
        terms=terms if isinstance(terms, list) else [],
        term_count=item.term_count,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _draft_read(item: AcademicTranslationDraft) -> AcademicTranslationDraftRead:
    return AcademicTranslationDraftRead(
        id=item.id,
        title=item.title,
        source_text=item.source_text,
        translated_text=item.translated_text,
        source_language=item.source_language,
        target_language=item.target_language,
        document_type=item.document_type,
        status=item.status,
        glossary_id=item.glossary_id,
        provider=item.provider,
        model=item.model,
        reviewed_at=item.reviewed_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/glossaries", response_model=TranslationGlossaryList)
def list_translation_glossaries(
    db: Annotated[Session, Depends(get_db)],
) -> TranslationGlossaryList:
    items = db.scalars(
        select(TranslationGlossary).order_by(TranslationGlossary.updated_at.desc())
    ).all()
    return TranslationGlossaryList(items=[_glossary_read(item) for item in items])


@router.post(
    "/glossaries",
    response_model=TranslationGlossaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_translation_glossary(
    request: TranslationGlossaryCreate,
    db: Annotated[Session, Depends(get_db)],
) -> TranslationGlossaryRead:
    item = TranslationGlossary(
        name=request.name,
        source_language=request.source_language,
        target_language=request.target_language,
        terms_json=json.dumps(
            [term.model_dump() for term in request.terms], ensure_ascii=False
        ),
        term_count=len(request.terms),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="同名且语言方向相同的术语库已经存在。",
        ) from exc
    db.refresh(item)
    return _glossary_read(item)


@router.put("/glossaries/{glossary_id}", response_model=TranslationGlossaryRead)
def update_translation_glossary(
    glossary_id: str,
    request: TranslationGlossaryCreate,
    db: Annotated[Session, Depends(get_db)],
) -> TranslationGlossaryRead:
    item = db.get(TranslationGlossary, glossary_id)
    if item is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    item.name = request.name
    item.source_language = request.source_language
    item.target_language = request.target_language
    item.terms_json = json.dumps(
        [term.model_dump() for term in request.terms], ensure_ascii=False
    )
    item.term_count = len(request.terms)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="同名且语言方向相同的术语库已经存在。",
        ) from exc
    db.refresh(item)
    return _glossary_read(item)


@router.delete("/glossaries/{glossary_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_translation_glossary(
    glossary_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    item = db.get(TranslationGlossary, glossary_id)
    if item is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    db.execute(
        update(AcademicTranslationDraft)
        .where(AcademicTranslationDraft.glossary_id == glossary_id)
        .values(glossary_id=None)
    )
    db.delete(item)
    db.commit()


@router.get("/drafts", response_model=AcademicTranslationDraftList)
def list_translation_drafts(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AcademicTranslationDraftList:
    total = db.scalar(select(func.count()).select_from(AcademicTranslationDraft)) or 0
    items = db.scalars(
        select(AcademicTranslationDraft)
        .order_by(AcademicTranslationDraft.updated_at.desc())
        .limit(limit)
    ).all()
    return AcademicTranslationDraftList(
        items=[_draft_read(item) for item in items], total=total
    )


@router.post(
    "/drafts",
    response_model=AcademicTranslationDraftRead,
    status_code=status.HTTP_201_CREATED,
)
def create_translation_draft(
    request: AcademicTranslationDraftCreate,
    db: Annotated[Session, Depends(get_db)],
) -> AcademicTranslationDraftRead:
    if request.glossary_id and db.get(TranslationGlossary, request.glossary_id) is None:
        raise HTTPException(status_code=409, detail="所选术语库不存在，请刷新后重试。")
    item = AcademicTranslationDraft(
        title=request.title,
        source_text=request.source_text,
        translated_text=request.translated_text,
        source_language=request.source_language,
        target_language=request.target_language,
        document_type=request.document_type,
        status=request.status,
        glossary_id=request.glossary_id,
        provider=request.provider,
        model=request.model,
        reviewed_at=datetime.now(UTC) if request.status == "reviewed" else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _draft_read(item)


@router.patch("/drafts/{draft_id}", response_model=AcademicTranslationDraftRead)
def update_translation_draft(
    draft_id: str,
    request: AcademicTranslationDraftUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> AcademicTranslationDraftRead:
    item = db.get(AcademicTranslationDraft, draft_id)
    if item is None:
        raise HTTPException(status_code=404, detail="译稿不存在。")
    if request.title is not None:
        item.title = request.title
    if request.translated_text is not None:
        item.translated_text = request.translated_text
        if request.status is None:
            item.status = "draft"
            item.reviewed_at = None
    if request.status is not None:
        item.status = request.status
        item.reviewed_at = datetime.now(UTC) if request.status == "reviewed" else None
    db.commit()
    db.refresh(item)
    return _draft_read(item)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_translation_draft(
    draft_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    item = db.get(AcademicTranslationDraft, draft_id)
    if item is None:
        raise HTTPException(status_code=404, detail="译稿不存在。")
    db.delete(item)
    db.commit()


@router.post("/translate", response_model=AcademicTranslationRead)
async def translate_academic_text(
    request: AcademicTranslationRequest,
) -> AcademicTranslationRead:
    try:
        provider = get_llm_provider()
        if not provider.supports_translation:
            raise LLMConfigurationError("学术翻译需要配置支持翻译的生成式 LLM。")
        return await AcademicTranslationService(provider).translate(request)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/outline", response_model=WritingOutlineRead)
async def generate_writing_outline(
    request: WritingOutlineRequest,
    db: Annotated[Session, Depends(get_db)],
) -> WritingOutlineRead:
    query = select(Document).where(Document.status == DocumentStatus.READY)
    if request.document_ids:
        query = query.where(Document.id.in_(request.document_ids))
    documents = list(db.scalars(query.order_by(Document.created_at.asc())).all())
    if request.document_ids:
        found = {item.id for item in documents}
        missing = [item for item in request.document_ids if item not in found]
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"有 {len(missing)} 篇文献不存在或尚未解析完成。",
            )
        order = {document_id: index for index, document_id in enumerate(request.document_ids)}
        documents.sort(key=lambda item: order[item.id])
    if not documents:
        raise HTTPException(status_code=409, detail="请先上传并解析至少一篇相关论文。")
    return await WritingOutlineService(db).generate(
        request.idea,
        documents,
        top_k=request.top_k,
        language=request.language,
    )
