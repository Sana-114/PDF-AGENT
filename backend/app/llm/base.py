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


@dataclass(slots=True)
class GeneratedTranslation:
    text: str


@dataclass(slots=True)
class GeneratedSegmentTranslations:
    items: dict[str, str]


class LLMProvider(Protocol):
    name: str
    model: str | None
    supports_translation: bool

    async def generate_grounded_answer(
        self, question: str, evidence: list[EvidenceAnchor]
    ) -> GeneratedAnswer: ...

    async def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> GeneratedTranslation: ...

    async def translate_segments(
        self,
        segments: list[tuple[str, str]],
        source_language: str,
        target_language: str,
    ) -> GeneratedSegmentTranslations: ...
