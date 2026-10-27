from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMResponseError
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document, DocumentStatus
from app.schemas.review import (
    FutureWorkAssessmentRead,
    FutureWorkAssessmentRequest,
    FutureWorkDecisionRead,
    FutureWorkDecisionRequest,
    ResearchReviewRead,
    ResearchReviewRequest,
)
from app.services.evidence_review import EvidenceReviewService
from app.services.future_progress import assess_progress
from app.services.future_work_review import save_decision

router = APIRouter()


@router.post("/future-directions/{direction_id}/assess", response_model=FutureWorkAssessmentRead)
async def assess_future_direction(
    direction_id: str, request: FutureWorkAssessmentRequest,
    db: Annotated[Session, Depends(get_db)],
) -> FutureWorkAssessmentRead:
    try:
        return await assess_progress(db, direction_id, request, get_llm_provider())
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="核对记录正在更新，请稍后手动重试。") from exc
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/future-directions/{direction_id}/decision", response_model=FutureWorkDecisionRead)
def confirm_future_direction(
    direction_id: str,
    request: FutureWorkDecisionRequest,
    db: Annotated[Session, Depends(get_db)],
) -> FutureWorkDecisionRead:
    try:
        return save_decision(db, direction_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/generate", response_model=ResearchReviewRead)
async def generate_research_review(
    request: ResearchReviewRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ResearchReviewRead:
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
        raise HTTPException(status_code=409, detail="文献库中没有已解析论文。")
    provider = ExtractiveProvider() if request.generation_mode == "extractive" else None
    return await EvidenceReviewService(db, provider=provider).generate(
        documents,
        match_threshold=request.match_threshold,
        max_evidence=request.max_evidence,
    )
