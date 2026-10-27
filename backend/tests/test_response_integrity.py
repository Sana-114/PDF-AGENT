"""Adversarial protocol checks require no provider, container or private document."""

import copy
import json

import httpx
import pytest

from app.llm.base import GeneratedClaim, LLMResponseError
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.response_integrity import strict_json
from app.schemas.agent import EvidenceAnchor


def envelope(text):
    return {"status": "completed", "output": [{
        "type": "message", "role": "assistant", "status": "completed",
        "phase": "final_answer", "content": [{"type": "output_text", "text": text}],
    }]}


def provider_for(response):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if isinstance(response, str):
            return httpx.Response(200, content=response.encode(), headers={
                "Content-Type": "application/json",
            })
        return httpx.Response(200, json=response)

    provider = DeepSeekResponsesProvider(
        api_key="test-placeholder", model="test", base_url="https://example.test",
        timeout_seconds=1, transport=httpx.MockTransport(handler),
    )
    return provider, calls


@pytest.mark.parametrize("text", [
    '{"status":"unsupported","status":"supported"}',
    '{"verdicts":[{"status":"uncertain","status":"supported"}]}',
    '{"evidence_ids":["E1"],"evidence_ids":["E2"]}',
    '{"translation":"first","translation":"second"}',
    '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}',
    '{"a":1', '{"a":"unfinished}', '{"a":1,}',
    'prose {"a":1}', '{"a":1} {"b":2}',
])
def test_strict_json_never_repairs_or_picks_a_duplicate_value(text):
    with pytest.raises((ValueError, TypeError)):
        strict_json(text)


def test_same_field_name_in_separate_objects_and_quoted_braces_are_not_duplicates():
    text = '{"rows":[{"status":"supported"},{"status":"uncertain"}],"text":"} {"}'
    assert strict_json(text) == json.loads(text)


@pytest.mark.parametrize("defect", [
    "incomplete", "failed", "in_progress", "queued", "unknown", "null_status",
    "incomplete_details", "error", "message_incomplete", "user_role", "commentary",
    "refusal", "tool_call", "two_messages", "invalid_output", "invalid_item",
    "invalid_content", "invalid_text", "empty_message",
])
def test_output_extraction_never_silently_selects_a_partial_or_ambiguous_message(defect):
    raw = envelope('{"translation":"complete-looking text"}')
    message = raw["output"][0]
    if defect in {"incomplete", "failed", "in_progress", "queued", "unknown"}:
        raw["status"] = defect
    elif defect == "null_status":
        raw["status"] = None
    elif defect == "incomplete_details":
        raw["incomplete_details"] = {"reason": "max_output_tokens"}
    elif defect == "error":
        raw["error"] = {"code": "failed"}
    elif defect == "message_incomplete":
        message["status"] = "incomplete"
    elif defect == "user_role":
        message["role"] = "user"
    elif defect == "commentary":
        message["phase"] = "commentary"
    elif defect == "refusal":
        message["content"].append({"type": "refusal", "refusal": "not permitted"})
    elif defect == "tool_call":
        raw["output"].append({"type": "function_call", "arguments": "{}"})
    elif defect == "two_messages":
        raw["output"].append(copy.deepcopy(message))
    elif defect == "invalid_output":
        raw["output"] = {}
    elif defect == "invalid_item":
        raw["output"].append(None)
    elif defect == "invalid_content":
        message["content"] = None
    elif defect == "invalid_text":
        message["content"][0]["text"] = {"translation": "not text"}
    else:
        message["content"] = []
    with pytest.raises(LLMResponseError):
        DeepSeekResponsesProvider._extract_output_text(raw)


def test_completed_reasoning_item_and_multiple_text_parts_remain_supported():
    raw = envelope('{"translation":')
    raw["output"][0]["content"].append({"type": "output_text", "text": '"ok"}'})
    raw["output"].insert(0, {"type": "reasoning", "summary": []})
    assert DeepSeekResponsesProvider._translation_json(raw) == {"translation": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", ["answer", "translation", "segments", "future", "review"])
@pytest.mark.parametrize("defect", ["incomplete", "duplicate"])
async def test_all_live_workflows_fail_closed_without_an_automatic_retry(workflow, defect):
    bodies = {
        "answer": '{"answer":"","claims":[]}',
        "translation": '{"translation":"translated"}',
        "segments": '{"translations":[{"block_id":"b","translation":"translated"}]}',
        "future": '{"facets":[]}',
        "review": '{"verdicts":[{"claim_index":0,"status":"supported","reason":"ok"}]}',
    }
    text = bodies[workflow]
    if defect == "duplicate":
        # Duplicate a legal field at the same level, preserving otherwise valid JSON.
        key = {"answer": "claims", "translation": "translation", "segments": "translations",
               "future": "facets", "review": "verdicts"}[workflow]
        value = json.loads(text)[key]
        text = text[:-1] + "," + json.dumps(key) + ":" + json.dumps(value) + "}"
    response = envelope(text)
    if defect == "incomplete":
        response["status"] = "incomplete"
    provider, calls = provider_for(response)
    evidence = [EvidenceAnchor(evidence_id="E1", document_id="one", page_number=1,
                               score=1, quote="Original evidence.")]
    with pytest.raises(LLMResponseError):
        if workflow == "answer":
            await provider.generate_grounded_answer("What is reported?", evidence)
        elif workflow == "translation":
            await provider.translate_text("source", "en", "zh")
        elif workflow == "segments":
            await provider.translate_segments([("b", "source")], "en", "zh")
        elif workflow == "future":
            await provider.assess_future_work("Future work should evaluate new tasks.", [])
        else:
            await provider.review_claims([GeneratedClaim("Original claim.", ["E1"])], evidence)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    '[]', '{"status":"failed","status":"completed","output":[]}',
    '{"output":[],"nested":{"status":NaN}}',
])
async def test_outer_transport_json_cannot_override_an_error_status(raw):
    provider, calls = provider_for(raw)
    with pytest.raises(LLMResponseError):
        await provider._post_response({"model": "test"})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_general_answer_rejects_a_non_object_without_attribute_error():
    provider, calls = provider_for(envelope("[]"))
    with pytest.raises(LLMResponseError) as exc:
        await provider.generate_grounded_answer("What is reported?", [])
    assert exc.value.raw_grounded_output["unparsed_text"] == "[]"
    assert len(calls) == 1
