from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.agent import AgentTraceStep, AnswerClaim, EvidenceAnchor


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=180)
    document_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("document_ids")
    @classmethod
    def deduplicate_document_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ConversationAskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    top_k: int = Field(default=6, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("问题不能为空。")
        return cleaned


class ConversationMessageRead(BaseModel):
    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    trace: list[AgentTraceStep] = Field(default_factory=list)
    insufficient_evidence: bool = False
    provider: str | None = None
    model: str | None = None
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    document_ids: list[str]
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageRead]


class ConversationTurnRead(BaseModel):
    conversation: ConversationSummary
    user_message: ConversationMessageRead
    assistant_message: ConversationMessageRead
