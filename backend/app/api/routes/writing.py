from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document, DocumentStatus
from app.schemas.writing import WritingOutlineRead, WritingOutlineRequest
from app.services.writing_outline import WritingOutlineService

router = APIRouter()


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
