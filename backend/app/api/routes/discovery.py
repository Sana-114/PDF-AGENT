import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.paper_source import PaperSource
from app.schemas.discovery import (
    PaperImportRequest,
    PaperImportResponse,
    PaperSearchResponse,
)
from app.schemas.document import DocumentRead
from app.services.document_ingestion import dispatch_document_parse, persist_staged_document
from app.services.paper_discovery import (
    PaperDiscoveryError,
    PaperDiscoveryService,
    candidate_filename,
    paper_discovery,
)

router = APIRouter()


def get_paper_discovery_service() -> PaperDiscoveryService:
    return paper_discovery


@router.get("/papers", response_model=PaperSearchResponse)
async def search_papers(
    q: Annotated[str, Query(min_length=2, max_length=500)],
    service: Annotated[PaperDiscoveryService, Depends(get_paper_discovery_service)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> PaperSearchResponse:
    try:
        query_kind, items, warnings = await service.search(q, limit)
    except PaperDiscoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return PaperSearchResponse(query=q, query_kind=query_kind, items=items, warnings=warnings)


@router.post(
    "/import",
    response_model=PaperImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_paper(
    request: PaperImportRequest,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[PaperDiscoveryService, Depends(get_paper_discovery_service)],
) -> PaperImportResponse:
    try:
        candidate = await service.resolve_import(request.source, request.source_id)
        staged = await service.download(candidate)
    except PaperDiscoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    result = persist_staged_document(
        db,
        staged,
        original_filename=candidate_filename(candidate),
    )
    document = result.document
    if not document.title:
        document.title = candidate.title
    if not document.arxiv_id:
        document.arxiv_id = candidate.arxiv_id
        document.arxiv_version = candidate.arxiv_version

    provenance = db.get(PaperSource, document.id)
    if provenance is None:
        provenance = PaperSource(
            document_id=document.id,
            provider=candidate.source,
            source_id=candidate.source_id,
            pdf_url=candidate.pdf_url or "",
            metadata_json="{}",
        )
        db.add(provenance)
    provenance.provider = candidate.source
    provenance.source_id = candidate.source_id
    provenance.doi = candidate.doi
    provenance.arxiv_id = candidate.arxiv_id
    provenance.landing_url = candidate.landing_url
    provenance.pdf_url = candidate.pdf_url or ""
    provenance.license = candidate.license
    provenance.metadata_json = json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False)
    db.commit()
    db.refresh(document)

    if not result.exact_duplicate:
        dispatch_document_parse(db, document)
    message = (
        "PDF 内容已存在，未重复下载入库；已更新论文来源信息。"
        if result.exact_duplicate
        else "开放 PDF 已安全下载入库，解析任务已创建。"
    )
    return PaperImportResponse(
        document=DocumentRead.model_validate(document),
        source=candidate,
        exact_duplicate=result.exact_duplicate,
        message=message,
    )
