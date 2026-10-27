import copy
import json

import pytest

from app.llm.base import GeneratedClaim, LLMResponseError
from app.llm.openai_responses import OpenAIResponsesProvider
from app.llm.review_limits import THEMATIC_REVIEW_PREFIX
from app.llm.thematic_review import render_thematic_review
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.review_themes import published_review_themes


def evidence():
    return [
        EvidenceAnchor(
            evidence_id=key,
            document_id=doc,
            page_number=1,
            quote="A supported method comparison.",
            score=1,
            source_type=source,
        )
        for key, doc, source in [
            ("R1", "one", "text"),
            ("R2", "two", "text"),
            ("R3", "one", "text"),
            ("G1", "two", "reference"),
        ]
    ]


def row(**updates):
    return {
        "topic": "T1",
        "kind": "cross_paper",
        "status": "supported",
        "text": "一种方法强调检索，另一种强调生成。",
        "evidence_ids": ["R1", "R2"],
        **updates,
    }


def review_payload(rows):
    return {"answer": "", "topics": [{"id": "T1", "title": "方法选择"}], "claims": rows}


@pytest.mark.parametrize(
    "bad",
    [
        row(evidence_ids=["R1", "R3"]),
        row(evidence_ids=["R1", "G1"]),
        row(evidence_ids=["R1", "F1"]),
        row(evidence_ids=["R1", "invented"]),
        row(status="unavailable"),
        row(kind={}),
        row(status=[]),
        row(topic=""),
        row(text="x" * 161),
        row(kind="single_paper"),
    row(text="一种方法支持检索，但其余对齐机制在本证据中不可得。"),
    row(text="P2指出一种方法强调检索，P3则强调生成。"),
    ],
)
def test_invalid_item_is_withheld_without_losing_valid_items_or_raw(bad):
    raw = review_payload([row(), bad])
    untouched = copy.deepcopy(raw)
    result = render_thematic_review(raw, evidence())
    assert len(result.claims) == 1 and result.field_rejections
    assert result.raw_grounded_output == untouched == raw


def test_unavailable_topic_does_not_force_a_comparison_or_fabricate_a_claim():
    raw = review_payload([row(status="unavailable", text=None, evidence_ids=[])])
    result = render_thematic_review(raw, evidence())
    assert result.claims == [] and result.field_rejections


def test_theme_metadata_never_reinserts_held_claims():
    generated = [
        GeneratedClaim("Keep [R1 R2]", ["R1", "R2"], "Methods", "cross_paper"),
        GeneratedClaim("Hold", ["R1"], "Scope", "single_paper"),
    ]
    claims = [AnswerClaim(text="Keep", evidence_ids=["R1", "R2"])]
    groups = published_review_themes(generated, claims, evidence())
    assert [g.title for g in groups] == ["Methods"]
    assert groups[0].claim_indices == groups[0].cross_paper_claim_indices == [0]


@pytest.mark.asyncio
async def test_thematic_schema_is_explicit_role_only_and_one_generation_call():
    provider = OpenAIResponsesProvider(
        api_key="test-placeholder", model="test", base_url="https://example.test", timeout_seconds=1
    )
    captured = []

    async def respond(payload):
        captured.append(payload)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(review_payload([row()])),
                        }
                    ],
                }
            ],
        }

    provider._post_response = respond
    result = await provider.generate_grounded_answer(
        THEMATIC_REVIEW_PREFIX + "概述方法。", evidence()
    )
    assert len(captured) == len(result.claims) == 1
    assert result.claims[0].review_kind == "cross_paper"
    assert "[R1] paper=P1;" in captured[0]["input"]
    assert "[R3] paper=P1;" in captured[0]["input"]
    assert "[R2] paper=P2;" in captured[0]["input"]
    instructions = captured[0]["instructions"]
    assert "TOTAL row limit across BOTH arrays and ALL topics" in instructions
    assert "two to four genuine multi-paper contrasts" in instructions
    assert "cannot be labelled single_paper" in instructions
    assert "not merely juxtapose two abstracts" in instructions
    assert "at most two single_paper rows in the entire review" in instructions
    assert "initialization-time, parameter-location and expectation qualifiers" in instructions
    assert "Different constraints do not prove compatibility" in instructions
    assert "explicitly supports that combination" in instructions
    assert "Preserve each planned feature's future status" in instructions
    schema = captured[0]["text"]["format"]["schema"]
    assert set(schema["required"]) == {"answer", "topics", "comparisons", "background"}
    assert schema["properties"]["background"]["maxItems"] == 2
    assert schema["properties"]["comparisons"]["maxItems"] == 6
    assert set(schema["properties"]["comparisons"]["items"]["required"]) == {
        "topic",
        "status",
        "text",
        "evidence_ids",
    }


def test_invalid_root_does_not_publish_free_narrative():
    with pytest.raises(LLMResponseError):
        render_thematic_review({"answer": "Unchecked facts", "claims": []}, evidence())


@pytest.mark.parametrize(
    "topics",
    [
        [{"id": "T1", "title": "Methods"}] * 2,
        [{"id": "T1", "title": "Methods"}, {"id": "T2", "title": "Methods"}],
        [{"id": "T5", "title": "Unexpected"}],
        [{"id": "T1", "title": "x" * 41}],
    ],
)
def test_bad_theme_registry_is_not_repaired_silently(topics):
    raw = review_payload([row()])
    raw["topics"] = topics
    with pytest.raises(LLMResponseError):
        render_thematic_review(raw, evidence())


def test_unregistered_topic_cannot_publish_a_valid_looking_claim():
    result = render_thematic_review(review_payload([row(topic="T4")]), evidence())
    assert result.claims == [] and result.field_rejections


def grouped_payload(comparisons=None, background=None):
    return {"answer": "", "topics": [{"id": "T1", "title": "机制与约束"}],
            "comparisons": comparisons or [], "background": background or []}


def grouped_row(ids):
    return {key: value for key, value in row(evidence_ids=ids).items() if key != "kind"}


def test_grouped_review_retains_explicit_roles_and_exact_raw_payload():
    raw = grouped_payload([grouped_row(["R1", "R2"])], [grouped_row(["R3"])])
    unchanged = copy.deepcopy(raw)
    result = render_thematic_review(raw, evidence())
    assert [c.review_kind for c in result.claims] == ["cross_paper", "single_paper"]
    assert result.raw_grounded_output == raw == unchanged


def test_grouped_comparison_cannot_use_two_methods_from_one_original_paper():
    result = render_thematic_review(grouped_payload([grouped_row(["R1", "R3"])]), evidence())
    assert result.claims == [] and result.field_rejections


def test_grouped_background_cannot_smuggle_a_multi_source_comparison():
    result = render_thematic_review(grouped_payload(background=[grouped_row(["R1", "R2"])]),
                                    evidence())
    assert result.claims == [] and result.field_rejections


@pytest.mark.parametrize("bucket,count", [("comparisons", 7), ("background", 3)])
def test_grouped_count_limits_reject_without_truncating(bucket, count):
    raw = grouped_payload()
    raw[bucket] = [grouped_row(["R1", "R2"])] * count
    unchanged = copy.deepcopy(raw)
    with pytest.raises(LLMResponseError, match="未截断"):
        render_thematic_review(raw, evidence())
    assert raw == unchanged


def test_grouped_malformed_row_is_held_without_rewriting_other_rows():
    raw = grouped_payload([row()], [grouped_row(["R1"])])
    result = render_thematic_review(raw, evidence())
    assert len(result.claims) == len(result.field_rejections) == 1
    assert result.claims[0].review_kind == "single_paper"


def test_grouped_unavailable_comparison_never_forces_a_fabricated_relation():
    raw = grouped_payload([{"topic": "T1", "status": "unavailable", "text": None,
                            "evidence_ids": []}])
    assert render_thematic_review(raw, evidence()).claims == []
