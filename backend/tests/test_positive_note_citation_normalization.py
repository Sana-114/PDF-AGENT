import copy

import pytest

from app.llm.base import LLMResponseError
from app.llm.model_fields import (
    normalize_positive_note_citations,
    render_valid_numeric_fields,
    requested_numeric_fields,
)
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import checked_claims


def item(note="E2 说明基线配置，E3/E1 所指早期模型。", ids=None, value="800 tokens"):
    return {
        "status": "inferred",
        "value": value,
        "source_note": note,
        "evidence_ids": ["E2", "E3"] if ids is None else ids,
    }


def test_only_existing_explicit_positive_id_is_normalized_and_original_kept():
    raw = item()
    saved = copy.deepcopy(raw)
    result, appended = normalize_positive_note_citations(raw, {"E1", "E2", "E3", "E9"})
    assert result["evidence_ids"] == ["E2", "E3", "E1"] and appended == ["E1"]
    assert "E9" not in result["evidence_ids"] and raw == saved
    assert result["source_note"] == raw["source_note"] and result["value"] == raw["value"]


@pytest.mark.parametrize(
    "note",
    [
        "E1 不单独作为此结论的证据。",
        "E1 没有提供该值。",
        "E1 is not used as evidence.",
        "E1 may be a useful candidate.",
        "E1 可能支持该配置。",
        "E1 might support this configuration.",
        "模型论文支持此结论。",
        "The model uses the E1 equation.",
    ],
)
def test_negative_uncertain_or_merely_matching_token_is_not_rescued(note):
    result, appended = normalize_positive_note_citations(item(note), {"E1", "E2", "E3"})
    assert not appended and result["evidence_ids"] == ["E2", "E3"]


def test_unknown_inline_id_is_not_invented_even_when_positive():
    with pytest.raises(LLMResponseError):
        normalize_positive_note_citations(item("E99 明确指出配置。"), {"E1", "E2", "E3"})


@pytest.mark.parametrize("ids", [["E99"], [1], "E2", None])
def test_malformed_explicit_array_is_not_repaired(ids):
    raw = item()
    raw["evidence_ids"] = ids
    result, appended = normalize_positive_note_citations(raw, {"E1", "E2", "E3"})
    assert result == raw and not appended


def test_normalization_is_opt_in_and_keeps_value_checks_afterwards():
    fields = requested_numeric_fields("GPT-1 上下文窗口多少？")
    raw = {"GPT-1:model_context": item(value="98765 tokens")}
    with pytest.raises(LLMResponseError):
        render_valid_numeric_fields(raw, fields, {"E1", "E2", "E3"}, chinese=True)
    log = {}
    claims, rejected = render_valid_numeric_fields(
        raw, fields, {"E1", "E2", "E3"}, chinese=True, citation_normalizations=log
    )
    assert not rejected and log == {"GPT-1:model_context": ["E1"]}
    evidence = [
        EvidenceAnchor(
            evidence_id=f"E{n}",
            document_id="public",
            page_number=n,
            quote="The model has a context window of 800 tokens.",
            score=1,
        )
        for n in (1, 2, 3)
    ]
    accepted, removed = checked_claims(claims, evidence)
    assert not accepted and removed == 1  # Adding an explicit ID cannot supply a numerical answer.


def test_rejected_quantity_does_not_leave_a_successful_normalization_log():
    fields = requested_numeric_fields("GPT-1 上下文窗口多少？")
    log = {}
    with pytest.raises(LLMResponseError):
        render_valid_numeric_fields(
            {"GPT-1:model_context": item(value="800 to 1600")},
            fields,
            {"E1", "E2", "E3"},
            chinese=True,
            citation_normalizations=log,
        )
    assert not log
