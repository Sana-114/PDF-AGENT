import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import (
    GeneratedAnswer,
    GeneratedClaim,
    GeneratedSegmentTranslations,
    GeneratedTranslation,
    LLMResponseError,
)
from app.schemas.agent import AskRequest, EvidenceAnchor
from app.services.academic_translation import translate_protected_segments, translate_protected_text
from app.services.grounded_text import checked_claims, render_checked_claims


def anchor(evidence_id="E1", quote="The original model used 8 GPUs."):
    return EvidenceAnchor(
        evidence_id=evidence_id, document_id="public-paper", page_number=2, quote=quote, score=1
    )


def test_render_uses_only_checked_claims_and_their_own_sources():
    claims, rejected = checked_claims(
        [
            GeneratedClaim(text="The model used 8 GPUs. [E1]", evidence_ids=["E1"]),
            GeneratedClaim(text="The model used 64 GPUs.", evidence_ids=["E1"]),
            GeneratedClaim(text="A made-up conclusion.", evidence_ids=["E999"]),
            GeneratedClaim(text="Fake inline citation [E999]", evidence_ids=["E1"]),
        ],
        [anchor(), anchor("E2", "An unrelated model used 64 GPUs.")],
    )
    assert rejected == 3
    assert render_checked_claims(claims) == "The model used 8 GPUs. [E1]"


def test_empty_checked_claims_publish_safe_insufficiency_message():
    claims, rejected = checked_claims(
        [GeneratedClaim(text="99 GPUs", evidence_ids=["E1"])], [anchor()]
    )
    assert claims == [] and rejected == 1
    assert "证据不足" in render_checked_claims(claims)
    assert "99" not in render_checked_claims(claims)


def test_numeric_guard_normalizes_pdf_decimal_spacing_without_guessing_values():
    claims, rejected = checked_claims(
        [
            GeneratedClaim(text="Adam uses 0.9 and 0.98.", evidence_ids=["E1"]),
            GeneratedClaim(text="Adam uses 0.99.", evidence_ids=["E1"]),
        ],
        [anchor(quote="Adam used beta_1 = 0 . 9 and beta_2 = 0 . 98.")],
    )
    assert rejected == 1
    assert len(claims) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_agent_never_leaks_unchecked_body_and_reports_real_provider(fail):
    class Registry:
        async def execute(self, *args):
            return [anchor()]

    class Provider:
        name = "cloud"
        model = "test"

        async def generate_grounded_answer(self, question, evidence):
            if fail:
                raise LLMResponseError("network unavailable")
            return GeneratedAnswer(
                answer="Uncited fabricated study in 2099.",
                claims=[GeneratedClaim(text="The model used 8 GPUs.", evidence_ids=["E1"])],
            )

    result = await ResearchAgent(None, registry=Registry(), provider=Provider()).ask(
        AskRequest(question="How many GPUs?")
    )
    assert "2099" not in result.answer
    assert "[E1]" in result.answer
    assert result.provider == ("extractive" if fail else "cloud")
    assert result.model == (None if fail else "test")
    assert result.trace[-1].status == ("fallback" if fail else "ok")


class EchoTranslator:
    async def translate_segments(self, segments, source_language, target_language):
        return GeneratedSegmentTranslations(items={key: "译文 " + text for key, text in segments})

    async def translate_text(self, text, source_language, target_language):
        return GeneratedTranslation(text="Translation: " + text)


@pytest.mark.asyncio
async def test_reader_translation_preserves_display_math_code_citations_and_numbers():
    text = "Method $$y = x^2\n+ 1$$ uses `beta_2=0.98`, 91.5% [11] and https://example.org/paper."
    result = await translate_protected_segments(EchoTranslator(), [("block-1", text)], "en", "zh")
    assert result.items["block-1"] == "译文 " + text
    selected = await translate_protected_text(EchoTranslator(), text, "en", "zh")
    assert selected.text == "Translation: " + text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "extra", "blank", "drop_token", "duplicate_token"])
async def test_reader_rejects_invalid_blocks_or_protected_token_mutation(mode):
    class InvalidTranslator:
        async def translate_segments(self, segments, *args):
            value = segments[0][1]
            if mode == "missing":
                return GeneratedSegmentTranslations(items={})
            if mode == "extra":
                return GeneratedSegmentTranslations(items={"b": value, "extra": "invented"})
            if mode == "blank":
                return GeneratedSegmentTranslations(items={"b": " "})
            return GeneratedSegmentTranslations(
                items={
                    "b": value.replace(
                        "`__PAPERPILOT_A__`",
                        "" if mode == "drop_token" else "`__PAPERPILOT_A__`" * 2,
                    )
                }
            )

    with pytest.raises(LLMResponseError):
        await translate_protected_segments(
            InvalidTranslator(), [("b", "Equation $y=x$ [11]")], "en", "zh"
        )


@pytest.mark.asyncio
async def test_selection_translation_rejects_changed_formula():
    class InvalidTranslator:
        async def translate_text(self, *args):
            return GeneratedTranslation(text="The equation is y=99.")

    with pytest.raises(LLMResponseError, match="未完整保留"):
        await translate_protected_text(InvalidTranslator(), "The equation is $y=x$.", "en", "zh")
