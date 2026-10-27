import asyncio
import copy
import json
import subprocess
import sys

import httpx
import pytest

from app.llm.base import LLMInvalidCitationError, LLMResponseError
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.model_fields import (
    numeric_fields_schema,
    render_numeric_fields,
    requested_numeric_fields,
)
from app.schemas.agent import EvidenceAnchor
from app.services.answer_completeness import affirmative_model_values
from app.services.grounded_text import checked_claims


def fact(value="70M", *, status="inferred", ids=None):
    return {
        "status": status,
        "value": value,
        "source_note": "后文 GPT-2 的参数表和基线等价说明支持此推断。",
        "evidence_ids": ["E1"] if ids is None else ids,
    }


def fixture():
    fields = requested_numeric_fields("GPT-1 和 GPT-2 的参数量分别是多少？")
    return fields, {
        "GPT-1:model_parameters": fact(),
        "GPT-2:model_parameters": fact("450M", status="supported"),
    }


def test_slots_come_from_current_query_not_historical_models_or_fixed_answers():
    fields = requested_numeric_fields(
        "Old: GPT-8\n\nCurrent user question:\n从 GPT-1 到 GPT-3 上下文窗口多少？"
    )
    assert list(fields) == [f"GPT-{number}:model_context" for number in range(1, 4)]
    schema = json.dumps(numeric_fields_schema(fields))
    assert "512" not in schema and "1024" not in schema and "2048" not in schema
    assert requested_numeric_fields("GPT-4 的分位是多少？") == {}
    assert len(requested_numeric_fields("GPT-2 参数量和上下文窗口多少？")) == 2


def test_model_owned_values_render_as_explicit_inference_and_preserve_source_provenance():
    fields, raw = fixture()
    original = copy.deepcopy(raw)
    claims = render_numeric_fields(raw, fields, {"E1"}, chinese=True)
    assert affirmative_model_values(claims[0].text, "model_parameters") == {
        "GPT-1": {"quantity:70000000"}
    }
    assert affirmative_model_values(claims[1].text, "model_parameters") == {
        "GPT-2": {"quantity:450000000"}
    }
    assert "可推断为" in claims[0].text and "基线等价" in claims[0].text
    assert claims[0].evidence_ids == ["E1"]
    assert raw == original


def test_unavailable_is_not_forced_into_an_assignment_or_a_guess():
    fields, raw = fixture()
    raw["GPT-1:model_parameters"] = fact(None, status="unavailable")
    raw["GPT-1:model_parameters"]["source_note"] = "原文证据不足。"
    claims = render_numeric_fields(raw, fields, {"E1"}, chinese=True)
    assert "GPT-1" not in affirmative_model_values(claims[0].text, "model_parameters")
    raw["GPT-1:model_parameters"]["evidence_ids"] = []
    assert len(render_numeric_fields(raw, fields, {"E1"}, chinese=True)) == 1


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "foreign_model",
        "missing_status",
        "bad_status",
        "empty_note",
        "unavailable_value",
        "null_supported",
        "range",
        "formula",
        "tokens_as_parameters",
    ],
)
def test_invalid_or_ambiguous_slots_fail_without_filling_any_gold_value(change):
    fields, raw = fixture()
    item = raw["GPT-1:model_parameters"]
    if change == "missing":
        raw.pop("GPT-1:model_parameters")
    elif change == "foreign_model":
        raw["GPT-8:model_parameters"] = fact()
    elif change == "missing_status":
        item.pop("status")
    elif change == "bad_status":
        item["status"] = "guess"
    elif change == "empty_note":
        item["source_note"] = " "
    elif change == "unavailable_value":
        item["status"] = "unavailable"
    elif change == "null_supported":
        item["value"] = None
    else:
        item["value"] = {
            "range": "70M to 450M",
            "formula": "70M+450M",
            "tokens_as_parameters": "70M tokens",
        }[change]
    with pytest.raises(LLMResponseError):
        render_numeric_fields(raw, fields, {"E1"}, chinese=True)


@pytest.mark.parametrize("ids", [[], ["E999"], ["E1", "E999"], [1]])
def test_missing_or_foreign_citations_do_not_become_supported_facts(ids):
    fields, raw = fixture()
    raw["GPT-1:model_parameters"]["evidence_ids"] = ids
    with pytest.raises(LLMInvalidCitationError):
        render_numeric_fields(raw, fields, {"E1"}, chinese=True)


def test_well_formed_but_unsupported_numeric_value_still_fails_the_production_guard():
    fields = requested_numeric_fields("GPT-1 参数量多少？")
    raw = {"GPT-1:model_parameters": fact("80M", status="supported")}
    claim = render_numeric_fields(raw, fields, {"E1"}, chinese=True)[0]
    anchor = EvidenceAnchor(
        evidence_id="E1",
        document_id="public",
        chunk_id="c",
        page_number=2,
        quote="Parameters Layers: 70M 6.",
        score=0.8,
    )
    accepted, rejected = checked_claims([claim], [anchor])
    assert not accepted and rejected


@pytest.mark.parametrize(
    "value", ["450M parameters", "450 million parameters", "450M参数", "450M 个参数"]
)
def test_parameter_field_label_is_not_a_different_quantity(value):
    fields = requested_numeric_fields("GPT-2 参数量多少？")
    claim = render_numeric_fields(
        {"GPT-2:model_parameters": fact(value)}, fields, {"E1"}, chinese=True
    )[0]
    assert affirmative_model_values(claim.text, "model_parameters") == {
        "GPT-2": {"quantity:450000000"}
    }


@pytest.mark.parametrize("value", ["450M tokens", "450M/900M parameters", "450M+70M parameters"])
def test_parameter_label_compatibility_cannot_accept_training_or_combined_counts(value):
    fields = requested_numeric_fields("GPT-2 参数量多少？")
    with pytest.raises(LLMResponseError):
        render_numeric_fields({"GPT-2:model_parameters": fact(value)}, fields, {"E1"}, chinese=True)


@pytest.mark.parametrize(
    "value", ["1536", "1536 tokens", "1536词元", "1536 个 Token", "1536個tokens", "1,536 tokens"]
)
def test_context_units_are_rendered_without_changing_the_model_supplied_number(value):
    fields = requested_numeric_fields("GPT-2 的上下文窗口多少？")
    raw = {"GPT-2:model_context": fact(value, status="supported")}
    claim = render_numeric_fields(raw, fields, {"E1"}, chinese=False)[0]
    assert "1536 tokens" in claim.text
    assert affirmative_model_values(claim.text, "model_context") == {"GPT-2": {"1536"}}


@pytest.mark.parametrize("value", ["1536 个", "1,53 tokens", "1536 to 3072 tokens"])
def test_context_unit_compatibility_does_not_accept_malformed_counts_or_ranges(value):
    fields = requested_numeric_fields("GPT-2 的上下文窗口多少？")
    with pytest.raises(LLMResponseError):
        render_numeric_fields({"GPT-2:model_context": fact(value)}, fields, {"E1"}, chinese=True)


@pytest.mark.parametrize("cited", [False, True])
def test_note_sources_must_be_in_the_same_fact_citation_list(cited):
    fields = requested_numeric_fields("GPT-2 的参数量多少？")
    item = fact(ids=["E1", "E2"] if cited else ["E1"])
    item["source_note"] = "根据 [E1] 的表格及 E2 的定义。"
    if cited:
        assert render_numeric_fields(
            {"GPT-2:model_parameters": item}, fields, {"E1", "E2"}, chinese=True
        )[0].evidence_ids == ["E1", "E2"]
    else:
        with pytest.raises(LLMInvalidCitationError, match="未列入"):
            render_numeric_fields(
                {"GPT-2:model_parameters": item}, fields, {"E1", "E2"}, chinese=True
            )


@pytest.mark.parametrize("malformed", [False, True])
def test_provider_requests_complete_slots_and_rejects_legacy_free_prose(malformed):
    fields, raw = fixture()
    captured = {}
    output = {"answer": "summary is not published", "model_fields": raw}
    if malformed:
        output.pop("model_fields")
        output["claims"] = []

    def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(output)}],
                    }
                ]
            },
        )

    provider = DeepSeekResponsesProvider(
        api_key="local-test-placeholder",
        model="test",
        base_url="https://example.test",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )
    if malformed:
        with pytest.raises(LLMResponseError) as error:
            asyncio.run(
                provider.generate_grounded_answer("GPT-1 和 GPT-2 的参数量分别是多少？", [])
            )
        assert error.value.raw_grounded_output == output
    else:
        evidence = [
            EvidenceAnchor(
                evidence_id="E1",
                document_id="public",
                chunk_id="c",
                page_number=2,
                quote="Parameters: 70M, 450M.",
                score=0.8,
            )
        ]
        response = asyncio.run(
            provider.generate_grounded_answer("GPT-1 和 GPT-2 的参数量分别是多少？", evidence)
        )
        assert len(response.claims) == 2
        assert response.raw_grounded_output == output
    assert captured["text"]["format"]["schema"]["properties"]["model_fields"]["required"] == list(
        fields
    )
    assert "required slot does not require guessing" in captured["instructions"]
    assert "claims" not in captured["text"]["format"]["schema"]["properties"]


@pytest.mark.parametrize("module", ["app.agent.harness", "app.services.evidence_fields"])
def test_clean_process_import_order_does_not_form_a_provider_service_cycle(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
