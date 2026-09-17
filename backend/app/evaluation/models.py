from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DocumentSelector(BaseModel):
    document_id: str | None = None
    filename: str | None = None
    title_contains: str | None = None
    arxiv_id: str | None = None
    arxiv_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_identity(self) -> "DocumentSelector":
        if not any((self.document_id, self.filename, self.title_contains, self.arxiv_id)):
            raise ValueError("文献选择器至少需要一个标识字段。")
        if self.arxiv_version is not None and self.arxiv_id is None:
            raise ValueError("arxiv_version 必须与 arxiv_id 一起使用。")
        return self


class EvidenceExpectation(BaseModel):
    text_contains_any: list[str] = Field(min_length=1)
    page_numbers: list[int] = Field(default_factory=list)
    source_types: list[
        Literal["text", "abstract", "table", "figure", "formula", "reference"]
    ] = Field(default_factory=list)
    document_title_contains: str | None = None

    @field_validator("text_contains_any")
    @classmethod
    def normalize_text_options(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("text_contains_any 至少需要一个非空文本特征。")
        return normalized


class RetrievalCase(BaseModel):
    case_id: str = Field(min_length=1)
    question: str = Field(min_length=2)
    documents: list[DocumentSelector] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=50)
    expectations: list[EvidenceExpectation] = Field(min_length=1)


class EvaluationThresholds(BaseModel):
    min_case_pass_rate: float = Field(default=0.8, ge=0, le=1)
    min_evidence_recall: float = Field(default=0.8, ge=0, le=1)
    min_mean_reciprocal_rank: float = Field(default=0.5, ge=0, le=1)
    min_anchor_valid_rate: float = Field(default=1.0, ge=0, le=1)
    require_bbox: bool = True


class RetrievalEvaluationSet(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str = Field(min_length=1)
    description: str = ""
    thresholds: EvaluationThresholds = Field(default_factory=EvaluationThresholds)
    cases: list[RetrievalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> "RetrievalEvaluationSet":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("评测集中的 case_id 必须唯一。")
        return self


class AnswerExpectation(BaseModel):
    text_contains_any: list[str] = Field(min_length=1)

    @field_validator("text_contains_any")
    @classmethod
    def normalize_text_options(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("text_contains_any 至少需要一个非空答案特征。")
        return normalized


class GroundedRAGCase(BaseModel):
    case_id: str = Field(min_length=1)
    question: str = Field(min_length=2)
    documents: list[DocumentSelector] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    evidence_expectations: list[EvidenceExpectation] = Field(default_factory=list)
    answer_expectations: list[AnswerExpectation] = Field(default_factory=list)
    must_refuse: bool = False

    @model_validator(mode="after")
    def require_ground_truth_for_answerable_case(self) -> "GroundedRAGCase":
        if not self.must_refuse and (
            not self.evidence_expectations or not self.answer_expectations
        ):
            raise ValueError("可回答用例必须同时配置证据期望和答案期望。")
        if self.must_refuse and self.answer_expectations:
            raise ValueError("拒答用例不能配置答案期望。")
        return self


class GroundedRAGThresholds(BaseModel):
    min_case_pass_rate: float = Field(default=0.8, ge=0, le=1)
    min_answer_fact_recall: float = Field(default=0.8, ge=0, le=1)
    min_evidence_recall: float = Field(default=0.8, ge=0, le=1)
    min_anchor_valid_rate: float = Field(default=1.0, ge=0, le=1)
    min_citation_valid_rate: float = Field(default=1.0, ge=0, le=1)
    min_grounded_claim_rate: float = Field(default=1.0, ge=0, le=1)
    min_refusal_pass_rate: float = Field(default=1.0, ge=0, le=1)
    min_provider_match_rate: float = Field(default=1.0, ge=0, le=1)
    max_fallback_rate: float = Field(default=0.0, ge=0, le=1)
    require_bbox: bool = True


class GroundedRAGEvaluationSet(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str = Field(min_length=1)
    description: str = ""
    expected_provider: str | None = None
    thresholds: GroundedRAGThresholds = Field(default_factory=GroundedRAGThresholds)
    cases: list[GroundedRAGCase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> "GroundedRAGEvaluationSet":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("评测集中的 case_id 必须唯一。")
        return self
