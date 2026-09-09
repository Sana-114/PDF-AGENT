from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    error_message: str | None
    title: str | None
    page_count: int | None
    arxiv_id: str | None
    arxiv_version: int | None
    parser_name: str | None
    parser_version: str | None
    duplicate_of_id: str | None
    duplicate_score: float | None
    duplicate_recommendation: str | None
    created_at: datetime
    updated_at: datetime


class UploadResult(BaseModel):
    document: DocumentRead
    exact_duplicate: bool = False
    message: str


class DocumentList(BaseModel):
    items: list[DocumentRead]
    total: int
    limit: int
    offset: int


class DocumentProgressRead(BaseModel):
    document_id: str
    status: str
    completed_pages: int
    page_count: int | None
    percentage: float
    resumable: bool
    updated_at: str | None = None


class OutlineNodeRead(BaseModel):
    text: str
    level: int = Field(ge=1, le=3)
    page_number: int = Field(ge=1)
    block_id: str
    bbox: list[float] | None = None
    children: list["OutlineNodeRead"] = Field(default_factory=list)


class DocumentOutlineRead(BaseModel):
    document_id: str
    items: list[OutlineNodeRead]


class ReferenceRead(BaseModel):
    reference_id: str
    label: str
    text: str
    page_number: int = Field(ge=1)
    bbox: list[float] | None = None
    block_ids: list[str] = Field(default_factory=list)


class CitationMentionRead(BaseModel):
    citation_id: str
    label: str
    page_number: int = Field(ge=1)
    block_id: str
    bbox: list[float] | None = None
    context: str


class DocumentReferencesRead(BaseModel):
    document_id: str
    items: list[ReferenceRead]
    mentions: list[CitationMentionRead]


class HealthRead(BaseModel):
    status: str
    service: str
    version: str
