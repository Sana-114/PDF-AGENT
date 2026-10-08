from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.schemas.citation_graph import CitationGraphStatsRead


class ResearchReviewRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    match_threshold: float = Field(default=0.72, ge=0.5, le=1.0)
    max_evidence: int = Field(default=30, ge=4, le=60)
    generation_mode: Literal["configured", "extractive"] = "configured"

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


class FutureWorkSource(BaseModel):
    document_id: str = Field(min_length=1, max_length=36)
    page_number: int = Field(ge=1)
    block_ids: list[str] = Field(min_length=1, max_length=5)
    quote: str = Field(min_length=10, max_length=1600)

    @field_validator("quote")
    @classmethod
    def meaningful_quote(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("证据摘录不能是空白或不足 10 个字符。")
        return value.strip()


class FutureWorkDecisionRequest(BaseModel):
    source: FutureWorkSource
    decision: Literal["open", "needs_review", "confirmed_addressed"]
    reason: str = Field(min_length=10, max_length=2000)
    progress: list[FutureWorkSource] = Field(default_factory=list, max_length=3)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("请填写至少 10 个字符的核验说明。")
        return value.strip()


class FutureWorkDecisionRead(BaseModel):
    direction_id: str
    decision: Literal["open", "needs_review", "confirmed_addressed"]
    reason: str
    progress_evidence: list[EvidenceAnchor] = Field(default_factory=list)
    updated_at: datetime


class FutureDirectionRead(BaseModel):
    direction_id: str
    text: str
    source_document_id: str
    source_title: str
    source_year: int | None
    source_evidence_id: str
    status: Literal["open", "possibly_addressed", "confirmed_addressed"]
    possibly_addressed_by_document_ids: list[str] = Field(default_factory=list)
    progress_evidence_ids: list[str] = Field(default_factory=list)
    exploration_score: float = Field(ge=0.0, le=1.0)
    reason: str
    confirmation: FutureWorkDecisionRead | None = None


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
