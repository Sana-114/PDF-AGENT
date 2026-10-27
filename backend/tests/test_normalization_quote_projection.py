import asyncio

import pytest

from app.agent.harness import ResearchAgent
from app.schemas.agent import AskRequest, EvidenceAnchor
from app.services.normalization_evidence import missing_normalization_quotes
from scripts.replay_official_failure_repairs import SavedProvider, SavedRegistry

QUOTE = ("Layer normalization (Smith et al., 2020) was moved to the input of each sub-block, "
         "and an additional layer normalization was added after the final self-attention block.")


def source(text=QUOTE):
    return EvidenceAnchor(evidence_id="E1", document_id="public", page_number=4, quote=text, score=1)


def test_fallback_is_original_sentence_with_its_own_anchor_not_invented_origin():
    claims = missing_normalization_quotes("LN 位置有什么调整？", [source()], [])
    assert len(claims) == 1 and QUOTE in claims[0].text and claims[0].evidence_ids == ["E1"]
    assert "之后移到" not in claims[0].text


@pytest.mark.parametrize("text", [
    "Layer normalization is used extensively throughout the model.",
    "Layer normalization was not moved to the input.",
    "If layer normalization was moved to the input, it might improve learning.",
    "Layer normalization should be moved to the input.",
])
def test_usage_negation_and_hypotheticals_never_become_a_modification_answer(text):
    assert not missing_normalization_quotes("LN 有什么调整？", [source(text)], [])


def test_harness_retains_exact_direct_changes_after_rejecting_an_unsupported_origin():
    response = asyncio.run(ResearchAgent(None, registry=SavedRegistry([source()]),
        provider=SavedProvider("saved", {"answer": "", "claims": [
            {"text": "GPT-2 将 Layer Normalization 从每个子模块之后移到了输入端。", "evidence_ids": ["E1"]}
        ]})).ask(AskRequest(question="GPT-2 的 LN 位置做了什么调整？", document_ids=["public"])))
    assert not response.insufficient_evidence and len(response.claims) == 1
    assert QUOTE in response.claims[0].text
    assert any(step.skill == "quote_explicit_normalization_changes" for step in response.trace)


def test_unrelated_question_does_not_append_a_normalization_excerpt():
    assert not missing_normalization_quotes("GPU 型号是什么？", [source()], [])
