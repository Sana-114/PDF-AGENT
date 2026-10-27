import copy
import json

import httpx
import pytest

from app.llm.base import LLMResponseError
from app.llm.comparison_review import (
    comparison_review_input,
    comparison_review_schema,
    validate_comparison_review,
)
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.schemas.agent import EvidenceAnchor

QUESTION = "比较 Alpha 与 Beta：两者如何处理输入？训练使用什么目标？"
DRAFT = "Alpha keeps its original representation; Beta stores quantized weights."
EDITED = "Alpha trains low-rank matrices; Beta trains adapters on quantized frozen weights."


def data():
    evidence = [EvidenceAnchor(evidence_id=key, document_id=doc, page_number=1, score=0.8,
                               quote=quote, source_type="text") for key, doc, quote in (
        ("E1", "one", "Alpha freezes base weights and trains small low-rank matrices."),
        ("E2", "two", "Beta stores frozen weights in quantized form and trains adapters."),
        ("E3", "two", "UNSELECTED source paragraph must not enter the editor request."),
    )]
    fields = {
        "comparison": {"status": "inferred", "text": DRAFT, "relation": "contrast",
                       "source_statements": [
            {"subject": "Alpha", "paper_id": "P1", "text": "Alpha trains low-rank matrices.",
             "evidence_ids": ["E1"]},
            {"subject": "Beta", "paper_id": "P2", "text": "Beta trains adapters on frozen weights.",
             "evidence_ids": ["E2"]},
        ]},
        "detail_1": {"status": "supported", "text": "Alpha trains low-rank matrices.",
                     "evidence_ids": ["E1"]},
    }
    return fields, evidence


def review():
    return {"status": "revised", "text": EDITED, "reason": "Remove unreported representation.",
            "source_checks": {key: {"status": "supported", "reason": "Own original supports it."}
                              for key in ("source_1", "source_2")}}


def test_live_compact_schema_preserves_three_checks_and_rejects_legacy_fallback():
    raw = {"checks": ["supported"] * 3}
    schema = comparison_review_schema()
    assert schema["required"] == ["checks"] and set(schema["properties"]) == {"checks"}
    assert schema["properties"]["checks"]["minItems"] == 3
    assert schema["properties"]["checks"]["maxItems"] == 3
    assert validate_comparison_review(raw, EDITED, wire_format=True) == ("supported", EDITED)
    assert validate_comparison_review(review(), DRAFT) == ("revised", EDITED)
    with pytest.raises(LLMResponseError):
        validate_comparison_review(review(), DRAFT, wire_format=True)


@pytest.mark.parametrize("position", range(3))
@pytest.mark.parametrize("verdict", ["supported", "unsupported", "uncertain"])
def test_each_live_check_is_required_and_raw_record_is_preserved(position, verdict):
    raw = {"checks": ["supported"] * 3}
    raw["checks"][position] = verdict
    before = copy.deepcopy(raw)
    expected = ("supported", EDITED) if verdict == "supported" else ("unavailable", None)
    assert validate_comparison_review(raw, EDITED, wire_format=True) == expected
    assert raw == before


@pytest.mark.parametrize("status", ["supported", "revised", "probably", None, []])
def test_live_review_rejects_legacy_or_unknown_self_reported_edit_status(status):
    raw = {"checks": ["supported"] * 3, "status": status}
    with pytest.raises(LLMResponseError):
        validate_comparison_review(raw, DRAFT, wire_format=True)


@pytest.mark.parametrize("checks", [[], ["supported"] * 2, ["supported"] * 4,
    ["supported", "supported", "probably"], [True, "supported", "supported"],
    [["supported"], "supported", "supported"], "supported", None])
def test_live_checklist_requires_exact_shape_and_enums(checks):
    with pytest.raises(LLMResponseError):
        validate_comparison_review({"checks": checks}, EDITED, wire_format=True)


@pytest.mark.parametrize("text", ["", "a" * 501, None, []])
def test_valid_checklist_cannot_authorize_empty_or_oversized_candidate(text):
    with pytest.raises(LLMResponseError):
        validate_comparison_review({"checks": ["supported"] * 3}, text, wire_format=True)


def test_editor_sees_only_explicitly_selected_originals_and_keeps_raw_source_unchanged():
    fields, evidence = data()
    before, originals = copy.deepcopy(fields), [e.model_dump() for e in evidence]
    payload = json.loads(comparison_review_input(QUESTION, fields["comparison"], evidence))
    assert "UNSELECTED" not in json.dumps(payload)
    assert payload["sources"][0]["selected_originals"][0]["quote"] == evidence[0].quote
    assert fields == before and [e.model_dump() for e in evidence] == originals
    assert validate_comparison_review(review(), DRAFT) == ("revised", EDITED)


@pytest.mark.parametrize("defect", [
    "extra", "missing_source", "uncertain", "unknown_status", "hidden_edit", "fake_edit",
    "empty", "long", "guessed_unavailable", "reason_type", "list",
])
def test_invalid_review_cannot_authorize_publication(defect):
    raw = review()
    if defect == "extra":
        raw["evidence_ids"] = ["E99"]
    elif defect == "missing_source":
        raw["source_checks"].pop("source_2")
    elif defect == "uncertain":
        raw["source_checks"]["source_2"]["status"] = "uncertain"
    elif defect == "unknown_status":
        raw["status"] = "probably"
    elif defect == "hidden_edit":
        raw["status"] = "supported"
    elif defect == "fake_edit":
        raw["text"] = DRAFT
    elif defect == "empty":
        raw["text"] = ""
    elif defect == "long":
        raw["text"] = "a" * 501
    elif defect == "guessed_unavailable":
        raw["status"] = "unavailable"
    elif defect == "reason_type":
        raw["source_checks"]["source_1"]["reason"] = []
    else:
        raw = []
    with pytest.raises(LLMResponseError):
        validate_comparison_review(raw, DRAFT)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["supported", "unavailable", "malformed",
                                  "transport", "new_algorithm", "new_number",
                                  "missing_brace", "duplicate_field", "incomplete"])
async def test_one_check_call_cannot_rewrite_comparison_retry_or_borrow_sources(mode):
    fields, evidence = data()
    fields["comparison"]["text"] = ""
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["text"]["format"]["name"] == "comparison_source_review":
            assert "UNSELECTED" not in payload["input"]
            if mode == "transport":
                return httpx.Response(500, json={"error": {"message": "Synthetic service error"}})
            body = {"checks": ["supported"] * 3}
            if mode == "unavailable":
                body["checks"][0] = "unsupported"
            elif mode == "malformed":
                body = {"unexpected": True}
            elif mode == "new_algorithm":
                body["text"] = "Alpha uses XYZ; Beta trains adapters."
            elif mode == "new_number":
                body["text"] = "Alpha trains 9999 steps; Beta trains adapters."
            assert payload["text"]["format"]["schema"]["required"] == ["checks"]
            assert "original representation" not in payload["input"]
        else:
            body = {"answer": "", "explanation_fields": fields}
        text, status = json.dumps(body), "completed"
        if payload["text"]["format"]["name"] == "comparison_source_review":
            if mode == "missing_brace":
                text = text[:-1]
            elif mode == "duplicate_field":
                text = text[:-1] + ',"checks":["supported","supported","supported"]}'
            elif mode == "incomplete":
                status = "incomplete"
        return httpx.Response(200, json={"status": status, "output": [{
            "type": "message", "content": [{"type": "output_text", "text": text}]}]})

    provider = DeepSeekResponsesProvider(api_key="test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1, transport=httpx.MockTransport(respond))
    result = await provider.generate_grounded_answer(QUESTION, evidence)
    assert len(requests) == 2
    assert result.raw_grounded_output["explanation_fields"]["comparison"]["text"] == ""
    if mode == "supported":
        assert len(result.claims) == 2 and not result.field_rejections
        assert "Alpha trains low-rank matrices." in result.claims[0].text
        assert "Beta trains adapters on frozen weights." in result.claims[0].text
        assert DRAFT not in result.claims[0].text
        assert result.claims[0].evidence_ids == ["E1", "E2"]
        assert result.comparison_review_status == "supported"
    else:
        assert len(result.claims) == 1 and set(result.field_rejections) == {"comparison"}
        assert result.claims[0].evidence_ids == ["E1"]
        assert not result.citation_normalizations
        assert result.comparison_review_status in {"unavailable", "failed"}
        diagnostics = result.raw_grounded_output["comparison_source_review_response"]
        if mode in {"missing_brace", "duplicate_field"}:
            assert diagnostics["unparsed_text"]
        elif mode == "incomplete":
            assert diagnostics["response_status"] == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", [DRAFT, "Alpha uses XYZ; Beta trains adapters."])
async def test_live_free_comparison_is_not_silently_repaired_or_sent_to_reviewer(draft):
    fields, evidence = data()
    fields["comparison"]["text"] = draft
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{
            "type": "output_text", "text": json.dumps({"answer": "", "explanation_fields": fields})
        }]}]})

    provider = DeepSeekResponsesProvider(api_key="test-placeholder", model="test",
        base_url="https://example.test", timeout_seconds=1, transport=httpx.MockTransport(respond))
    result = await provider.generate_grounded_answer(QUESTION, evidence)
    assert len(requests) == 1 and len(result.claims) == 1
    assert set(result.field_rejections) == {"comparison"}
    assert result.comparison_review_status is None
    assert result.raw_grounded_output["explanation_fields"]["comparison"]["text"] == draft
