import asyncio
import copy
import json

import httpx
import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import LLMResponseError
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.relationship_fields import (
    relationship_fields_schema,
    render_relationship_fields,
    requested_relationship_fields,
)
from app.schemas.agent import AskRequest, EvidenceAnchor
from scripts.replay_official_failure_repairs import SavedRegistry

QUESTION = "Child 承袭和丢弃 Parent 的哪些架构部分？"


def fixtures():
    raw = {
        "inheritance": {
            "status": "inferred",
            "text": "Child 沿用 Parent 的编码器结构。",
            "evidence_ids": ["E1", "E2"],
        },
        "removal": {
            "status": "inferred",
            "text": "Child 去掉 Parent 的解码器部分。",
            "evidence_ids": ["E1", "E2"],
        },
    }
    evidence = [
        EvidenceAnchor(
            evidence_id="E1",
            document_id="child",
            page_number=2,
            quote="Child is an encoder-only model based on Parent.",
            score=1,
        ),
        EvidenceAnchor(
            evidence_id="E2",
            document_id="parent",
            page_number=3,
            quote="Parent has an encoder and a decoder.",
            score=1,
        ),
        EvidenceAnchor(
            evidence_id="E3",
            document_id="child",
            page_number=3,
            quote="Child is a bidirectional encoder.",
            score=1,
        ),
    ]
    return raw, evidence


def test_two_slots_are_query_owned_and_schema_does_not_include_gold_answers():
    fields = requested_relationship_fields(QUESTION)
    assert list(fields) == ["inheritance", "removal"]
    assert not requested_relationship_fields("Child 只继承了什么？")
    assert not requested_relationship_fields(
        "Old inherits/discards\n\nCurrent user question:\nGPU 型号？"
    )
    assert "BERT" not in json.dumps(relationship_fields_schema(fields))


def test_valid_relations_remain_model_text_with_two_original_sources():
    raw, evidence = fixtures()
    original = copy.deepcopy(raw)
    claims, rejected = render_relationship_fields(
        raw, requested_relationship_fields(QUESTION), evidence, chinese=True
    )
    assert not rejected and len(claims) == 2 and raw == original
    assert all("架构推断" in claim.text and claim.evidence_ids == ["E1", "E2"] for claim in claims)


@pytest.mark.parametrize("defect", ["missing", "extra_comparison", "legacy_claims"])
def test_extra_or_legacy_free_prose_is_not_silently_published(defect):
    raw, evidence = fixtures()
    if defect == "missing":
        raw.pop("removal")
    elif defect == "extra_comparison":
        raw["other_model_comparison"] = {"text": "unrequested"}
    else:
        raw = {"claims": [{"text": "old extra narrative", "evidence_ids": ["E1", "E2"]}]}
    with pytest.raises(LLMResponseError):
        render_relationship_fields(
            raw, requested_relationship_fields(QUESTION), evidence, chinese=True
        )


@pytest.mark.parametrize(
    "defect", ["one_paper", "unknown_id", "uncited_inline", "bad_status", "no_text"]
)
def test_bad_field_is_rejected_but_valid_sibling_kept(defect):
    raw, evidence = fixtures()
    item = raw["inheritance"]
    if defect == "one_paper":
        item["evidence_ids"] = ["E1", "E3"]
    elif defect == "unknown_id":
        item["evidence_ids"] = ["E1", "E99"]
    elif defect == "uncited_inline":
        item["text"] += "E3 中描述该结构。"
    elif defect == "bad_status":
        item["status"] = []
    else:
        item["text"] = None
    claims, rejected = render_relationship_fields(
        raw, requested_relationship_fields(QUESTION), evidence, chinese=True
    )
    assert len(claims) == 1 and set(rejected) == {"inheritance"}


def test_unavailable_does_not_force_a_guess():
    raw, evidence = fixtures()
    for item in raw.values():
        item.update(status="unavailable", text=None, evidence_ids=[])
    assert render_relationship_fields(
        raw, requested_relationship_fields(QUESTION), evidence, chinese=True
    ) == ([], {})


@pytest.mark.parametrize("legacy", [False, True])
def test_real_provider_contract_is_local_checked_and_legacy_failure_is_visible(legacy):
    raw, evidence = fixtures()
    captured = []
    output = {"answer": "", "relationship_fields": raw}
    if legacy:
        output = {
            "answer": "old narrative",
            "claims": [{"text": "incidental comparison", "evidence_ids": ["E1", "E2"]}],
        }

    def respond(request):
        captured.append(json.loads(request.content))
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
    response = asyncio.run(
        ResearchAgent(None, registry=SavedRegistry(evidence), provider=provider).ask(
            AskRequest(question=QUESTION, document_ids=["child", "parent"], top_k=18)
        )
    )
    assert len(captured) == 1
    schema = captured[0]["text"]["format"]["schema"]
    assert (
        schema["required"] == ["answer", "relationship_fields"]
        and "claims" not in schema["properties"]
    )
    if legacy:
        assert response.insufficient_evidence and response.provider == "extractive"
        assert all(claim.text != "incidental comparison" for claim in response.claims)
    else:
        assert not response.insufficient_evidence and len(response.claims) == 2
