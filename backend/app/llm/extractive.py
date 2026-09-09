from app.llm.base import (
    GeneratedAnswer,
    GeneratedClaim,
    GeneratedSegmentTranslations,
    GeneratedTranslation,
    LLMConfigurationError,
)
from app.schemas.agent import EvidenceAnchor


class ExtractiveProvider:
    """Deterministic, no-key fallback that only restates retrieved passages."""

    name = "extractive"
    model = None

    async def generate_grounded_answer(
        self, question: str, evidence: list[EvidenceAnchor]
    ) -> GeneratedAnswer:
        del question
        if not evidence:
            return GeneratedAnswer(
                answer="当前文献中没有检索到足以回答该问题的证据。",
                claims=[],
            )

        selected = evidence[:3]
        claims = [
            GeneratedClaim(text=item.quote, evidence_ids=[item.evidence_id]) for item in selected
        ]
        answer = "\n\n".join(
            f"{item.quote} [{item.evidence_id}]" for item in selected
        )
        return GeneratedAnswer(answer=answer, claims=claims)

    async def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> GeneratedTranslation:
        del text, source_language, target_language
        raise LLMConfigurationError(
            "划词翻译需要生成式 LLM；请在 .env 中配置 LLM_PROVIDER、LLM_MODEL 和 LLM_API_KEY。"
        )

    async def translate_segments(
        self,
        segments: list[tuple[str, str]],
        source_language: str,
        target_language: str,
    ) -> GeneratedSegmentTranslations:
        del segments, source_language, target_language
        raise LLMConfigurationError(
            "双语阅读需要生成式 LLM；请在 .env 中配置 LLM_PROVIDER、LLM_MODEL 和 LLM_API_KEY。"
        )
