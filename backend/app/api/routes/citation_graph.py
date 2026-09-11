from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document, DocumentStatus
from app.schemas.citation_graph import CitationGraphRead, CitationGraphRequest
from app.services.citation_graph import CitationGraphService, citation_graph

router = APIRouter()


def get_citation_graph_service() -> CitationGraphService:
    return citation_graph


@router.post("", response_model=CitationGraphRead)
def build_citation_graph(
    request: CitationGraphRequest,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CitationGraphService, Depends(get_citation_graph_service)],
) -> CitationGraphRead:
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
    return service.build(documents, match_threshold=request.match_threshold)
