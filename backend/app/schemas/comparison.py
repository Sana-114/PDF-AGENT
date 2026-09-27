from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.agent import AgentTraceStep, EvidenceAnchor

ComparisonRelation = Literal[
    "agreement",
    "difference",
    "conflict",
    "single_source",
    "unclassified",
]


class CrossPaperComparisonRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    document_ids: list[str] = Field(min_length=2, max_length=6)
    evidence_per_document: int = Field(default=5, ge=1, le=6)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("对比问题不能为空。")
        return cleaned

    @field_validator("document_ids")
    @classmethod
    def deduplicate_document_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def require_two_distinct_documents(self) -> "CrossPaperComparisonRequest":
        if len(self.document_ids) < 2:
            raise ValueError("跨论文对比至少需要两篇不同文献。")
        return self


class ComparisonPaperRead(BaseModel):
    document_id: str
    title: str
    evidence_ids: list[str]
    evidence_count: int
    candidate_count: int = 0
    evidence_page_count: int = 0
    source_types: list[str] = Field(default_factory=list)
    coverage: Literal["supported", "no_evidence"]


class ComparisonClaimRead(BaseModel):
    text: str
    dimension: str = "综合"
    relation: ComparisonRelation
    evidence_ids: list[str]
    document_ids: list[str]


class ComparisonMatrixCellRead(BaseModel):
    document_id: str
    title: str
    status: Literal["cited", "not_cited"]
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class ComparisonMatrixRowRead(BaseModel):
    dimension: str
    relation: ComparisonRelation
    statement: str
    cells: list[ComparisonMatrixCellRead]


class ComparisonAuditRead(BaseModel):
    generated_claim_count: int = 0
    accepted_claim_count: int = 0
    rejected_claim_count: int = 0
    derived_claim_count: int = 0
    published_claim_citation_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_utilization: float = Field(default=0.0, ge=0.0, le=1.0)
    referenced_document_count: int = 0
    cross_document_claim_count: int = 0
    rejection_reasons: list[str] = Field(default_factory=list)


class ComparisonStatsRead(BaseModel):
    agreement_count: int = 0
    difference_count: int = 0
    conflict_count: int = 0
    single_source_count: int = 0
    unclassified_count: int = 0
    supported_document_count: int = 0


class CrossPaperComparisonRead(BaseModel):
    question: str
    answer: str
    papers: list[ComparisonPaperRead]
    claims: list[ComparisonClaimRead]
    matrix: list[ComparisonMatrixRowRead] = Field(default_factory=list)
    evidence: list[EvidenceAnchor]
    stats: ComparisonStatsRead
    audit: ComparisonAuditRead = Field(default_factory=ComparisonAuditRead)
    insufficient_evidence: bool
    provider: str
    model: str | None = None
    trace: list[AgentTraceStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
