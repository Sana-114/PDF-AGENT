"""A supported partial answer does not erase its explicit missing why-field."""

import asyncio

import pytest

from app.agent.harness import ResearchAgent
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.services.answer_completeness import missing_fields
from scripts.replay_official_failure_repairs import SavedProvider, SavedRegistry


@pytest.mark.parametrize("text", [
    "证据中未给出为何该目标允许重复更新的机制说明。",
    "提供的摘录没有说明该方法有效的原因。",
    "The evidence does not explain why the objective allows repeated updates.",
])
def test_explicit_missing_explanation_is_not_marked_complete(text):
    assert missing_fields("为什么可以重复更新？", [AnswerClaim(text=text, evidence_ids=["E1"])], [])


@pytest.mark.parametrize("question,text", [
    ("更新了几轮？", "证据未给出有效的机制说明。"),
    ("为什么有效？", "这避免了训练时梯度不稳定，并且无需额外阶段。"),
    ("为什么有效？", "该方法不依赖人工标注的推理轨迹。"),
    ("历史问题：为什么有效？\n\nCurrent user question:\n用了几轮？",
     "证据未给出有效的机制说明。"),
])
def test_non_refusal_negative_or_historical_why_is_not_mislabeled(question, text):
    assert not missing_fields(question, [AnswerClaim(text=text, evidence_ids=["E1"])], [])


def test_harness_keeps_partial_claims_and_marks_explicit_missing_explanation():
    evidence = [EvidenceAnchor(evidence_id="E1", document_id="doc", page_number=1,
                              quote="A clipped objective enables multiple updates.", score=1)]
    raw = {"answer": "部分结果", "claims": [
        {"text": "使用裁剪目标执行多轮更新。", "evidence_ids": ["E1"]},
        {"text": "证据未给出为何该目标有效的逐步推导。", "evidence_ids": ["E1"]},
    ]}
    response = asyncio.run(ResearchAgent(None, registry=SavedRegistry(evidence),
        provider=SavedProvider("cached", raw)).ask(AskRequest(question="为什么能重复更新？")))
    assert response.insufficient_evidence
    assert len(response.claims) == 2
    assert "尚未完整回答" in response.answer
    assert response.trace[-1].status == "incomplete"
