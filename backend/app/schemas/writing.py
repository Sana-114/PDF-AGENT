from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.agent import AnswerClaim, EvidenceAnchor


class WritingOutlineRequest(BaseModel):
    idea: str = Field(min_length=8, max_length=3000)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=12, ge=4, le=20)
    language: Literal["zh", "en"] = "zh"

    @field_validator("idea")
    @classmethod
    def clean_idea(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("document_ids")
    @classmethod
    def deduplicate_document_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class WritingSectionRead(BaseModel):
    section_id: Literal["abstract", "introduction", "related_work"]
    title: str
    content_markdown: str
    evidence_ids: list[str]


class VerifiedReferenceRead(BaseModel):
    citation_key: str
    document_id: str
    title: str
    authors: list[str]
    publication_year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    landing_url: str | None
    provenance: str
    verified_fields: list[str]
    formatted_citation: str
    evidence_ids: list[str]


class WritingOutlineRead(BaseModel):
    proposed_title: str
    idea: str
    sections: list[WritingSectionRead]
    references: list[VerifiedReferenceRead]
    claims: list[AnswerClaim]
    evidence: list[EvidenceAnchor]
    provider: str
    model: str | None = None
    insufficient_evidence: bool
    warnings: list[str] = Field(default_factory=list)
