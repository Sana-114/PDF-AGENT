from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class EvidenceAnchor(BaseModel):
    """A stable pointer back to text extracted from a PDF."""

    evidence_id: str
    chunk_id: str | None = None
    document_id: str
    document_title: str | None = None
    page_number: int = Field(ge=1)
    block_ids: list[str] = Field(default_factory=list)
    bbox: list[float] | None = None
    section: str | None = None
    source_type: Literal["text", "abstract", "table", "figure", "formula", "reference"] = "text"
    retrieval_mode: Literal["lexical", "vector", "hybrid", "reranked", "local_context"] = "lexical"
    # Reranking is a separate stage; it must not erase the first-stage provenance.
    recall_mode: Literal["lexical", "vector", "hybrid"] | None = None
    quote: str
    score: float = Field(ge=0.0, le=1.0)
    # A page-local continuation is not a separately ranked retrieval hit.
    context_for: list[str] = Field(default_factory=list)


class AnswerClaim(BaseModel):
    text: str
    evidence_ids: list[str]


class GroundingRejection(BaseModel):
    claim_index: int = Field(ge=0)
    evidence_ids: list[str]
    reasons: list[str]


class GroundingAudit(BaseModel):
    """The grounding input, not the final answer or a semantic accuracy score."""

    input_claim_count: int = Field(ge=0)
    accepted_claim_count: int = Field(ge=0)
    document_claim_counts: dict[str, int] = Field(default_factory=dict)
    rejections: list[GroundingRejection] = Field(default_factory=list)


class AgentTraceStep(BaseModel):
    skill: str
    status: str
    summary: str
    duration_ms: int = Field(ge=0)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    retrieval_query: str | None = Field(default=None, min_length=2, max_length=8000)
    document_ids: list[str] | None = None
    top_k: int = Field(default=6, ge=1, le=20)

    @field_validator("document_ids")
    @classmethod
    def deduplicate_document_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))


class AskResponse(BaseModel):
    answer: str
    claims: list[AnswerClaim]
    evidence: list[EvidenceAnchor]
    insufficient_evidence: bool
    provider: str
    model: str | None = None
    trace: list[AgentTraceStep] = Field(default_factory=list)
    grounding_audit: GroundingAudit | None = None


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    source_language: Literal["auto", "zh", "en"] = "auto"
    target_language: Literal["zh", "en"]

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("待翻译文本不能为空。")
        return cleaned

    @model_validator(mode="after")
    def reject_same_language(self) -> "TranslateRequest":
        if self.source_language != "auto" and self.source_language == self.target_language:
            raise ValueError("源语言与目标语言不能相同。")
        return self


class TranslateResponse(BaseModel):
    translation: str
    source_language: Literal["auto", "zh", "en"]
    target_language: Literal["zh", "en"]
    provider: str
    model: str | None = None


class SkillRead(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    requires_llm: bool


class AgentStatus(BaseModel):
    provider: str
    model: str | None
    llm_configured: bool
    translation_configured: bool
    retrieval_mode: str
    embedding_provider: str
    embedding_model: str
    reranker_provider: str
    reranker_model: str | None
    skills: list[str]
