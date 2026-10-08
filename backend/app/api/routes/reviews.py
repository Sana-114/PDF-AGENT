from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document, DocumentStatus
from app.schemas.review import (
    FutureWorkDecisionRead,
    FutureWorkDecisionRequest,
    ResearchReviewRead,
    ResearchReviewRequest,
)
from app.services.evidence_review import EvidenceReviewService
from app.services.future_work_review import save_decision

router = APIRouter()


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
    return await EvidenceReviewService(db).generate(
        documents,
        match_threshold=request.match_threshold,
        max_evidence=request.max_evidence,
    )
