from datetime import datetime

from pydantic import BaseModel, ConfigDict


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


class HealthRead(BaseModel):
    status: str
    service: str
    version: str
