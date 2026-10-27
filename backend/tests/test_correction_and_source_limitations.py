import asyncio

import pytest

from app.agent.harness import ResearchAgent
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.services.grounded_text import only_source_limitations, unsupported_numeric_facts
from scripts.replay_official_failure_repairs import SavedProvider, SavedRegistry


def source(text="The optimizer coefficient is 0.73."):
    return EvidenceAnchor(
        evidence_id="E1", document_id="public", page_number=1, quote=text, score=1
    )


@pytest.mark.parametrize(
    "text",
    [
        "系数为 0.73，而不是 0.61。",
        "系数不是 0.61，而是 0.73。",
        "β₂ 的设置为 0.73而非0.61。",
        "The coefficient is 0.73, not 0.61.",
        "The coefficient is not 0.61 but 0.73.",
    ],
)
def test_explicit_correction_only_exempts_negated_value(text):
    assert not unsupported_numeric_facts(text, [source()])


@pytest.mark.parametrize(
    "text",
    [
        "系数不是 0.61。",
        "系数是 0.61。",
        "系数可能是 0.61 或 0.73。",
        "系数为 0.72，而不是 0.61。",
        "系数为 0.73，而不是 0.61；使用 99 块 GPU。",
        "系数为 0.73，而不是 0.61；另一个参数也设置为 0.61。",
    ],
)
def test_denials_guesses_and_other_affirmations_are_still_checked(text):
    assert unsupported_numeric_facts(text, [source()])


@pytest.mark.parametrize(
    "text",
    [
        "所提供的证据中没有报告训练时随机种子的具体整数值。",
        "当前原文没有提供该信息。",
        "The provided evidence does not report the training seed.",
    ],
)
def test_cited_missing_source_is_still_a_non_answer(text):
    assert only_source_limitations([AnswerClaim(text=text, evidence_ids=["E1"])])


@pytest.mark.parametrize(
    "text",
    [
        "该模型没有使用监督微调。",
        "提供的证据未报告该值，但后续比较可推断为 73。",
        "当前证据没有提供该信息。模型使用两阶段训练。",
        "The provided evidence does not report it; however the comparison establishes it.",
    ],
)
def test_negative_finding_or_caveat_with_answer_is_not_pure_refusal(text):
    assert not only_source_limitations([AnswerClaim(text=text, evidence_ids=["E1"])])


def test_harness_preserves_transparent_limitation_and_marks_insufficient():
    anchor = source("The model is trained with an optimizer.")
    provider = SavedProvider(
        "cached",
        {
            "answer": "unchecked",
            "claims": [
                {
                    "text": "所提供的证据中没有报告训练时随机种子的具体整数值。",
                    "evidence_ids": ["E1"],
                },
            ],
        },
    )
    response = asyncio.run(
        ResearchAgent(None, registry=SavedRegistry([anchor]), provider=provider).ask(
            AskRequest(question="训练随机种子的具体整数值是多少？", document_ids=["public"])
        )
    )
    assert response.insufficient_evidence
    assert len(response.claims) == 1
    assert response.trace[-1].skill == "audit_answerability"


def test_supported_different_field_does_not_turn_mixed_answer_into_pure_refusal():
    claims = [
        AnswerClaim(text="当前证据没有报告随机种子。", evidence_ids=["E1"]),
        AnswerClaim(text="优化器系数为 0.73。", evidence_ids=["E1"]),
    ]
    assert not only_source_limitations(claims)
