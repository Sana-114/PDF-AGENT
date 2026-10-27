import asyncio
import json

import httpx
import pytest

from app.llm.base import GeneratedClaim
from app.llm.openai_responses import OpenAIResponsesProvider
from app.schemas.agent import EvidenceAnchor
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.grounded_text import checked_claims, unsupported_numeric_facts


def anchor(quote):
    return EvidenceAnchor(evidence_id="E1", document_id="doc", page_number=1,
                          quote=quote, score=1)


def test_spelled_count_matches_digits_without_changing_raw_quote():
    evidence = [anchor("PaLM 540B with just eight chain-of-thought exemplars on GSM8K.")]
    claim = GeneratedClaim(text="PaLM 540B 使用 8 个示例。", evidence_ids=["E1"])
    accepted, rejected = checked_claims([claim], evidence)
    assert rejected == 0 and accepted[0].text == claim.text
    assert "eight" in evidence[0].quote


@pytest.mark.parametrize("claim", ["使用 9 个示例。", "Uses nine exemplars."])
def test_wrong_small_count_still_rejected(claim):
    assert unsupported_numeric_facts(claim, [anchor("Uses eight exemplars.")]) == ["9"]


@pytest.mark.parametrize("source", ["twenty one examples", "eight hundred samples",
                                    "one million parameters", "eighth example",
                                    "model-eight tokens", "one of the methods"])
def test_compounds_ordinals_and_noncounts_do_not_supply_small_number(source):
    assert unsupported_numeric_facts("Uses 1 or 8 examples.", [anchor(source)]) == ["1", "8"]


SOURCE = ("We bypass the supervised fine-tuning (SFT) phase before RL training. "
          "Our model (referred to as Example-R2- Zero) develops reasoning.")


@pytest.mark.parametrize("text", ["Example-R2 跳过了 SFT。", "该设计绕过监督微调阶段。",
                                 "Example-R2 skips supervised fine-tuning before RL."])
def test_omitted_stage_cannot_be_generalized_from_named_variant(text):
    assert claim_semantic_conflicts(text, [anchor(SOURCE)]) == [
        "omitted_training_stage_requires_named_variant"]


def test_named_variant_and_contrast_are_preserved():
    assert not claim_semantic_conflicts("Example-R2-Zero 跳过了 SFT。", [anchor(SOURCE)])
    assert not claim_semantic_conflicts("Example-R2 uses SFT and RL.", [anchor(SOURCE)])
    assert not claim_semantic_conflicts("Example-R2 没有跳过 SFT。", [anchor(SOURCE)])
    assert not claim_semantic_conflicts("Example-R2 does not skip SFT.", [anchor(SOURCE)])
    # Unknown naming remains pending source review, not 'entailed'.
    assert not claim_semantic_conflicts("Example-R2 skips SFT.", [anchor("We bypass SFT.")])


def test_grounded_chinese_language_and_explicit_variant_prompt():
    captured = {}

    def respond(request):
        captured.update(json.loads(request.content))
        content = json.dumps({"answer": "结果",
            "claims": [{"text": "中文结果", "evidence_ids": ["E1"]}]})
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": content}]}]})

    provider = OpenAIResponsesProvider(api_key="local-test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1,
        transport=httpx.MockTransport(respond))
    asyncio.run(provider.generate_grounded_answer("覆盖多少语言？", [anchor("46 languages")]))
    assert "每条 claims.text 必须用中文" in captured["instructions"]
    assert "repeat that exact variant name" in captured["instructions"]
    assert "at most four distinct" in captured["instructions"]


def test_history_language_does_not_override_current_english_question():
    captured = {}

    def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"answer":"none","claims":[]}'}]}]})

    provider = OpenAIResponsesProvider(api_key="local-test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1,
        transport=httpx.MockTransport(respond))
    asyncio.run(provider.generate_grounded_answer(
        "之前用中文\n\nCurrent user question:\nHow many layers?", [anchor("Eight layers.")]))
    assert "本次用户用中文" not in captured["instructions"]
