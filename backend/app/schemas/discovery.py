from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document import DocumentRead

PaperProvider = Literal["semantic_scholar", "arxiv", "crossref"]
QueryKind = Literal["title", "doi", "arxiv"]


class PaperCandidate(BaseModel):
    source: PaperProvider
    source_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    published_at: str | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    arxiv_version: int | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    license: str | None = None
    importable: bool = False
    import_reason: str | None = None


class PaperSearchResponse(BaseModel):
    query: str
    query_kind: QueryKind
    items: list[PaperCandidate]
    warnings: list[str] = Field(default_factory=list)


class PaperImportRequest(BaseModel):
    source: PaperProvider
    source_id: str = Field(min_length=1, max_length=256)


class PaperImportResponse(BaseModel):
    document: DocumentRead
    source: PaperCandidate
    exact_duplicate: bool = False
    message: str
