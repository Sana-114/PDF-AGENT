import copy
import json

import httpx
import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import LLMResponseError
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.explanation_fields import render_explanation_fields, requested_explanation_fields
from app.schemas.agent import AskRequest, EvidenceAnchor

QUESTION = "比较 Alpha 与 Beta：两者如何处理输入？训练使用什么目标？"


def evidence():
    return [EvidenceAnchor(evidence_id=key, document_id=doc, page_number=1,
                           quote=quote, score=1, source_type=kind)
            for key, doc, kind, quote in (
                ("E1", "one", "text", "Alpha transforms inputs with a fixed encoder."),
                ("E2", "two", "text", "Beta transforms inputs with a learned encoder."),
                ("E3", "one", "text", "Alpha learns using its reported objective."),
                ("E4", "two", "reference", "An Alpha reference in Beta."))]


def raw_fields(*, supported=False, live=False):
    result = {
        "comparison": {"status": "inferred", "text": "Alpha与Beta使用不同的编码器处理输入。",
                       "evidence_ids": ["E1", "E2"]},
        "detail_1": {"status": "supported", "text": "Alpha使用所报告的训练目标学习。",
                     "evidence_ids": ["E3"]},
    }
    if supported:
        result["comparison"].pop("evidence_ids")
        result["comparison"]["source_statements"] = [
            {"subject": "Alpha", "paper_id": "P1", "text": "Alpha uses a fixed encoder.",
             "evidence_ids": ["E1"]},
            {"subject": "Beta", "paper_id": "P2", "text": "Beta uses a learned encoder.",
             "evidence_ids": ["E2"]},
        ]
    if live:
        result["comparison"]["text"] = ""
        result["comparison"]["relation"] = "contrast"
    return result


def review_response():
    return {"checks": ["supported"] * 3}


def test_slots_are_question_derived_not_a_method_answer_bank():
    fields = requested_explanation_fields(QUESTION, evidence())
    assert list(fields) == ["comparison", "detail_1"]
    assert "如何处理输入" in fields["comparison"]
    assert fields["detail_1"] == "训练使用什么目标"
    assert "encoder" not in json.dumps(fields)
    for question in ("比较 Alpha 与 Beta 的作者。", "Alpha如何处理输入？",
                     QUESTION + "\n\nCurrent user question:\n作者是谁？"):
        assert requested_explanation_fields(question, evidence()) == {}
    assert requested_explanation_fields(QUESTION, evidence()[:1]) == {}
    assert requested_explanation_fields("比较 Alpha 与 Alpha：如何处理输入？", evidence()) == {}


def test_comparison_focus_keeps_user_workflow_instead_of_only_subject_names():
    question = "比较 Alpha 与 Beta 的偏好学习流程：闭式结果是什么对象？"
    fields = requested_explanation_fields(question, evidence())
    assert "偏好学习流程" in fields["comparison"]
    assert "encoder" not in fields["comparison"] and "交叉熵" not in fields["comparison"]


def test_explicit_enumeration_has_separate_components_without_expected_actions():
    q = "比较 Alpha 与 Beta：两者如何处理输入？以及甲组件、乙组件和丙组件各自解决什么问题？"
    fields = requested_explanation_fields(q, evidence())
    assert len(fields) == 4
    assert all(part in fields[f"detail_{i}"]
               for i, part in enumerate(("甲组件", "乙组件", "丙组件"), 1))


def test_valid_slots_preserve_source_ids_raw_text_and_separate_inference():
    raw, anchors = raw_fields(), evidence()
    before, untouched = copy.deepcopy(raw), [e.model_dump() for e in anchors]
    claims, rejected = render_explanation_fields(
        raw, requested_explanation_fields(QUESTION, anchors), anchors, chinese=True
    )
    assert not rejected and len(claims) == 2
    assert claims[0].text == "据两篇原文推断：" + raw["comparison"]["text"]
    assert claims[0].evidence_ids == ["E1", "E2"]
    assert raw == before and [e.model_dump() for e in anchors] == untouched


@pytest.mark.parametrize("ids", [["E1"], ["E1", "E3"], ["E1", "E4"], ["E1", "E99"], []])
def test_comparison_cannot_borrow_other_slot_bibliography_or_unknown_evidence(ids):
    raw = raw_fields()
    raw["comparison"]["evidence_ids"] = ids
    claims, rejected = render_explanation_fields(
        raw, requested_explanation_fields(QUESTION, evidence()), evidence(), chinese=True
    )
    assert len(claims) == 1 and set(rejected) == {"comparison"}
    assert claims[0].evidence_ids == ["E3"]


@pytest.mark.parametrize("defect", ["extra", "missing", "list"])
def test_invalid_outer_structure_is_not_silently_filled(defect):
    raw = raw_fields()
    if defect == "extra":
        raw["unexpected"] = {}
    elif defect == "missing":
        raw.pop("detail_1")
    else:
        raw = []
    with pytest.raises(LLMResponseError):
        render_explanation_fields(raw, requested_explanation_fields(QUESTION, evidence()),
                                  evidence(), chinese=True)


def test_unavailable_slot_is_a_visible_gap_not_a_required_guess():
    raw = raw_fields()
    raw["detail_1"] = {"status": "unavailable", "text": None, "evidence_ids": []}
    claims, rejected = render_explanation_fields(
        raw, requested_explanation_fields(QUESTION, evidence()), evidence(), chinese=True
    )
    assert len(claims) == 1 and "detail_1" in rejected


@pytest.mark.parametrize("update", [
    {"status": []}, {"text": "x" * 501}, {"evidence_ids": [None]},
    {"text": "这个结论引用E99但没有提供。"}, {"evidence_ids": ["E4"]},
])
def test_one_bad_detail_does_not_remove_valid_comparison_or_add_citations(update):
    raw = raw_fields()
    raw["detail_1"].update(update)
    claims, rejected = render_explanation_fields(
        raw, requested_explanation_fields(QUESTION, evidence()), evidence(), chinese=True
    )
    assert len(claims) == 1 and set(rejected) == {"detail_1"}
    assert claims[0].evidence_ids == ["E1", "E2"]


@pytest.mark.asyncio
async def test_provider_single_contract_partial_fields_remain_incomplete_in_harness():
    requests = []
    raw = raw_fields(supported=True, live=True)
    raw["detail_1"] = {"status": "unavailable", "text": None, "evidence_ids": []}

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["text"]["format"]["name"] == "comparison_source_review":
            return httpx.Response(200, json={"output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(review_response())}]}]})
        schema = payload["text"]["format"]["schema"]
        assert schema["required"] == ["answer", "explanation_fields"]
        assert "claims" not in schema["properties"]
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"answer": "", "explanation_fields": raw})}
        ]}]})

    class Registry:
        async def execute(self, name, args, context):
            return [e for e in evidence() if e.document_id in args["document_ids"]]

    provider = DeepSeekResponsesProvider(api_key="test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1, transport=httpx.MockTransport(respond))
    result = await ResearchAgent(None, registry=Registry(), provider=provider).ask(
        AskRequest(question=QUESTION, document_ids=["one", "two"])
    )
    assert len(requests) == 2 and result.provider == "deepseek"
    assert len(result.claims) == 1 and result.insufficient_evidence
    assert "detail_1" in result.answer and "所给证据不足" in result.answer
    assert any(step.skill == "normalize_explicit_citations" for step in result.trace)


@pytest.mark.asyncio
async def test_unused_answer_prose_cannot_hide_valid_fields_or_be_published():
    def respond(request):
        payload = json.loads(request.content)
        if payload["text"]["format"]["name"] == "comparison_source_review":
            return httpx.Response(200, json={"output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(review_response())}]}]})
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({
                "answer": "Do not publish this unsupported 9999 detail.",
                "explanation_fields": raw_fields(supported=True, live=True),
            })}
        ]}]})

    provider = DeepSeekResponsesProvider(api_key="test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1, transport=httpx.MockTransport(respond))
    result = await provider.generate_grounded_answer(QUESTION, evidence())
    assert len(result.claims) == 2 and result.answer == ""
    assert "9999" in result.raw_grounded_output["answer"]
    assert all("9999" not in c.text for c in result.claims)
    assert result.citation_normalizations == {"comparison": ["E1", "E2"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["extra_root_slots", "duplicate_root_slots"])
async def test_repeated_nested_slots_at_root_are_not_repaired_or_sent_to_editor(defect):
    calls = []
    fields = raw_fields(supported=True)

    def respond(request):
        calls.append(json.loads(request.content))
        raw = {"answer": "", "explanation_fields": fields, **fields}
        text = json.dumps(raw)
        if defect == "duplicate_root_slots":
            text = text[:-1] + ',"detail_1":' + json.dumps(fields["detail_1"]) + "}"
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": text}]}]})

    provider = DeepSeekResponsesProvider(api_key="test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1, transport=httpx.MockTransport(respond))
    with pytest.raises(LLMResponseError):
        await provider.generate_grounded_answer(QUESTION, evidence())
    assert len(calls) == 1
