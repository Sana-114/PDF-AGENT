from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMResponseError
from app.models.document import Document, DocumentStatus
from app.schemas.academic_translation import (
    AcademicTranslationRead,
    AcademicTranslationRequest,
)
from app.schemas.writing import WritingOutlineRead, WritingOutlineRequest
from app.services.academic_translation import AcademicTranslationService
from app.services.writing_outline import WritingOutlineService

router = APIRouter()


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
