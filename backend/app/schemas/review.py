from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.schemas.citation_graph import CitationGraphStatsRead


class ResearchReviewRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    match_threshold: float = Field(default=0.72, ge=0.5, le=1.0)
    max_evidence: int = Field(default=30, ge=4, le=60)

    @field_validator("document_ids")
    @classmethod
    def deduplicate_document_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class ReviewEvidenceRead(EvidenceAnchor):
    evidence_kind: Literal["overview", "future_work", "later_progress"]


class ReviewPaperRead(BaseModel):
    document_id: str
    title: str
    publication_year: int | None
    graph_role: Literal["cornerstone", "bridge", "derivative", "isolated", "peripheral"]
    foundation_score: float
    overview_evidence_ids: list[str]
    future_work_evidence_ids: list[str]


class FutureDirectionRead(BaseModel):
    direction_id: str
    text: str
    source_document_id: str
    source_title: str
    source_year: int | None
    source_evidence_id: str
    status: Literal["open", "possibly_addressed"]
    possibly_addressed_by_document_ids: list[str] = Field(default_factory=list)
    progress_evidence_ids: list[str] = Field(default_factory=list)
    exploration_score: float = Field(ge=0.0, le=1.0)
    reason: str


class ResearchReviewRead(BaseModel):
    generated_at: datetime
    review: str
    claims: list[AnswerClaim]
    papers: list[ReviewPaperRead]
    future_directions: list[FutureDirectionRead]
    evidence: list[ReviewEvidenceRead]
    graph_stats: CitationGraphStatsRead
    provider: str
    model: str | None = None
    insufficient_evidence: bool
    warnings: list[str] = Field(default_factory=list)
