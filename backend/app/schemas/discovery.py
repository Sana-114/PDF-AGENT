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
    code_url: str | None = None
    code_stars: int | None = None
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


class ReferenceResolveRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=36)
    reference_ids: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=8, ge=1, le=20)
    candidates_per_reference: int = Field(default=3, ge=1, le=5)


class ReferenceCandidateMatch(BaseModel):
    paper: PaperCandidate
    match_score: float = Field(ge=0.0, le=1.0)
    match_reason: str


class ReferenceResolution(BaseModel):
    reference_id: str
    label: str
    text: str
    page_number: int = Field(ge=1)
    status: Literal["matched", "uncertain", "not_found", "error"]
    query_kind: QueryKind | None = None
    candidates: list[ReferenceCandidateMatch] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReferenceResolveResponse(BaseModel):
    document_id: str
    total_references: int
    attempted: int
    matched: int
    importable: int
    items: list[ReferenceResolution]
