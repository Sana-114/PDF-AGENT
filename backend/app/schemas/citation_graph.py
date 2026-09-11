from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CitationGraphRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    match_threshold: float = Field(default=0.72, ge=0.5, le=1.0)

    @model_validator(mode="after")
    def unique_document_ids(self):
        self.document_ids = list(dict.fromkeys(self.document_ids))
        return self


class CitationGraphNodeRead(BaseModel):
    document_id: str
    title: str
    authors: list[str]
    arxiv_id: str | None
    arxiv_version: int | None
    page_count: int | None
    in_degree: int
    out_degree: int
    pagerank: float
    foundation_score: float
    role: Literal["cornerstone", "bridge", "derivative", "isolated", "peripheral"]


class CitationGraphEdgeRead(BaseModel):
    edge_id: str
    source_document_id: str
    target_document_id: str
    reference_ids: list[str]
    reference_labels: list[str]
    reference_pages: list[int]
    sample_reference: str
    mention_count: int
    match_score: float
    match_reason: str


class CitationGraphStatsRead(BaseModel):
    document_count: int
    relation_count: int
    total_references: int
    matched_references: int
    unmatched_references: int
    cornerstone_count: int
    derivative_count: int
    density: float


class CitationGraphRead(BaseModel):
    generated_at: datetime
    nodes: list[CitationGraphNodeRead]
    edges: list[CitationGraphEdgeRead]
    stats: CitationGraphStatsRead
    warnings: list[str] = Field(default_factory=list)
