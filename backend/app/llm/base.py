from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.agent import EvidenceAnchor


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


@dataclass(slots=True)
class GeneratedClaim:
    text: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GeneratedAnswer:
    answer: str
    claims: list[GeneratedClaim]


class LLMProvider(Protocol):
    name: str
    model: str | None

    async def generate_grounded_answer(
        self, question: str, evidence: list[EvidenceAnchor]
    ) -> GeneratedAnswer: ...

