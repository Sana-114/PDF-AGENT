from typing import Any

from pydantic import BaseModel, Field, field_validator


class EvidenceAnchor(BaseModel):
    """A stable pointer back to text extracted from a PDF."""

    evidence_id: str
    document_id: str
    document_title: str | None = None
    page_number: int = Field(ge=1)
    block_ids: list[str] = Field(default_factory=list)
    bbox: list[float] | None = None
    section: str | None = None
    quote: str
    score: float = Field(ge=0.0, le=1.0)


class AnswerClaim(BaseModel):
    text: str
    evidence_ids: list[str]


class AgentTraceStep(BaseModel):
    skill: str
    status: str
    summary: str
    duration_ms: int = Field(ge=0)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
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


class SkillRead(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    requires_llm: bool

