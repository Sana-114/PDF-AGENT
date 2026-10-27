import asyncio

import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.services.answer_completeness import missing_fields
from app.services.grounded_text import unsupported_numeric_facts


def anchor(quote, key="E1", source_type="text"):
    return EvidenceAnchor(evidence_id=key, document_id="doc", page_number=1,
                          quote=quote, source_type=source_type, score=1)


@pytest.mark.parametrize("source,claim", [
    ("up to 7x increases in speed", "速度最多提高 7 倍"),
    ("measure 7x+ speedups", "报告了 7 倍以上加速"),
    ("100x fewer parameters", "参数少了 100 倍"),
    ("4× faster", "加速 4 倍"),
    ("298 / 400 (~90th)", "298/400，约第 90 百分位"),
    ("90th percentile", "约第 90 百分位"),
])
def test_literal_notation_equivalence(source, claim):
    assert not unsupported_numeric_facts(claim, [anchor(source)])


@pytest.mark.parametrize("source,claim", [
    ("up to 7x speed", "8 倍"), ("4× faster", "40 倍"),
    ("7xylophone", "7 倍"), ("model-7x", "7 倍"),
    ("top 10%", "第 90 百分位"),  # no implicit complement arithmetic
    ("~90th", "90%"),  # percentile numeral is not a literal percentage
])
def test_wrong_or_inferred_values_remain_rejected(source, claim):
    assert unsupported_numeric_facts(claim, [anchor(source)])


def test_comparison_only_answer_cannot_cover_requested_gpt4_percentile():
    claims = [AnswerClaim(text="GPT-3.5 得分处于后 10%。", evidence_ids=["E1"])]
    missing = missing_fields("GPT-4 的考试成绩处于什么分位？", claims, [anchor("top 10%")])
    assert len(missing) == 1 and "GPT-4" in missing[0]


def test_target_answer_does_not_need_a_paper_specific_gold_number():
    claims = [AnswerClaim(text="GPT-4 成绩处于前 10%。", evidence_ids=["E1"])]
    assert not missing_fields("GPT-4 的考试成绩处于什么分位？", claims, [anchor("top 10%")])


def test_causal_body_is_presented_first_without_changing_sources_or_scores():
    evidence = [anchor("The method supports multiple epochs.", "E1", "abstract"),
                anchor("Clipping removes the incentive for excessive changes.", "E2")]
    class FixedRegistry:
        async def execute(self, name, arguments, context):
            return evidence

    registry = FixedRegistry()

    class Capture:
        name, model, supports_translation = "test", "test", False
        received = None

        async def generate_grounded_answer(self, question, sources):
            self.received = sources
            return GeneratedAnswer(answer="", claims=[GeneratedClaim(
                text="Clipping removes the incentive for excessive changes.", evidence_ids=["E2"])])

    provider = Capture()
    response = asyncio.run(ResearchAgent(None, registry=registry, provider=provider).ask(
        AskRequest(question="为什么能够重复优化？")))
    assert provider.received == [evidence[1], evidence[0]]
    assert provider.received[0] is evidence[1]
    assert response.claims[0].evidence_ids == ["E2"]
