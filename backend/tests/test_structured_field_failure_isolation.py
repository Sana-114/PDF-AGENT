import asyncio
import copy
import json

import httpx
import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import LLMResponseError
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.model_fields import render_valid_numeric_fields, requested_numeric_fields
from app.schemas.agent import AskRequest, EvidenceAnchor
from scripts.replay_official_failure_repairs import SavedRegistry


def fixture():
    fields = requested_numeric_fields("GPT-1、GPT-2、GPT-3 的上下文窗口分别多少？")
    raw = {key: {"status": "supported", "value": f"{value} tokens",
                 "source_note": f"由 E{index} 的明确配置支持。", "evidence_ids": [f"E{index}"]}
           for index, (key, value) in enumerate(zip(fields, (800, 1600, 3200), strict=True), 1)}
    raw["GPT-1:model_context"]["source_note"] += "E4 不单独作为该值的证据。"
    return fields, raw


def test_uncited_negative_provenance_is_not_excused_or_automatically_cited():
    fields, raw = fixture()
    saved = copy.deepcopy(raw)
    claims, rejected = render_valid_numeric_fields(raw, fields, {"E1", "E2", "E3", "E4"}, chinese=True)
    assert set(rejected) == {"GPT-1:model_context"}
    assert len(claims) == 2 and all("GPT-1" not in claim.text for claim in claims)
    assert all("E4" not in claim.evidence_ids for claim in claims) and raw == saved


@pytest.mark.parametrize("defect", ["missing_slot", "extra_slot", "not_object", "all_invalid"])
def test_outer_structure_or_all_invalid_is_still_a_visible_error(defect):
    fields, raw = fixture()
    if defect == "missing_slot":
        raw.pop("GPT-1:model_context")
    elif defect == "extra_slot":
        raw["GPT-8:model_context"] = {}
    elif defect == "not_object":
        raw = []
    else:
        for item in raw.values():
            item["evidence_ids"] = ["E99"]
    with pytest.raises(LLMResponseError):
        render_valid_numeric_fields(raw, fields, {"E1", "E2", "E3", "E4"}, chinese=True)


@pytest.mark.parametrize("value", ["invented", "1600 to 3200", None])
def test_bad_quantity_removes_only_its_own_field(value):
    fields, raw = fixture()
    raw["GPT-1:model_context"]["evidence_ids"].append("E4")
    raw["GPT-2:model_context"]["value"] = value
    claims, rejected = render_valid_numeric_fields(raw, fields, {"E1", "E2", "E3", "E4"}, chinese=True)
    assert set(rejected) == {"GPT-2:model_context"} and len(claims) == 2


def test_provider_and_harness_publish_two_valid_fields_but_do_not_call_answer_complete():
    _, raw = fixture()
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"answer": "", "model_fields": raw})}]}]})
    provider = DeepSeekResponsesProvider(api_key="local-test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1, transport=httpx.MockTransport(respond))
    evidence = [EvidenceAnchor(evidence_id=f"E{index}", document_id=f"gpt{index}",
        document_title=f"GPT-{index}", page_number=1, score=1,
        quote=f"The GPT-{index} model uses a context window of {value} tokens.")
        for index, value in enumerate((800, 1600, 3200), 1)]
    evidence.append(EvidenceAnchor(evidence_id="E4", document_id="gpt1", page_number=2,
                                  score=1, quote="A training sequence uses 800 tokens."))
    response = asyncio.run(ResearchAgent(None, registry=SavedRegistry(evidence), provider=provider).ask(
        AskRequest(question="GPT-1、GPT-2、GPT-3 的上下文窗口分别多少？", top_k=18)))
    assert len(calls) == 1 and response.provider == "deepseek" and response.insufficient_evidence
    assert len(response.claims) == 2
    assert any("GPT-1" in step.summary and step.status == "incomplete" for step in response.trace
               if step.skill == "audit_answer_completeness")
    assert any(step.skill == "audit_structured_field_support" and step.status == "rejected"
               for step in response.trace)
