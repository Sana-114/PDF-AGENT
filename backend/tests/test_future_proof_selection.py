import copy

import pytest

from app.core.future_work import future_goal_quotes
from app.llm.base import LLMResponseError
from app.llm.future_assessment import materialize_proofs, progress_spans

GOALS = {"S0": "Original future plan."}


def offered():
    text = "We measure actual memory. The full finetuning perfor- mance is reported."
    return [{"evidence_id": "P1", "quote": text, "document_id": "one"}]


def wire(proof):
    return {"facets": [{"goal_span_id": "S0", "status": "partial",
                        "explanation": "所选范围存在部分对应。", "proof": proof}]}


def test_proof_selection_preserves_literal_word_wrap_without_retyping_or_mutating_input():
    inputs = offered()
    before = copy.deepcopy(inputs)
    rows, spans = progress_spans(inputs)
    assert inputs == before
    assert all(quote in inputs[0]["quote"] for _, quote in spans.values())
    key = next(key for key, (_, quote) in spans.items() if quote.startswith("The full"))
    original = wire([{"span_id": key}])
    result = materialize_proofs(original, spans, GOALS)
    assert result["facets"][0]["proof"] == [{
        "evidence_id": "P1", "quote": "The full finetuning perfor- mance is reported.",
    }]
    assert original["facets"][0]["proof"] == [{"span_id": key}]
    assert rows[0]["document_id"] == "one"


def test_two_actual_spans_map_to_the_same_original_identity_without_fusion():
    _, spans = progress_spans(offered())
    result = materialize_proofs(
        wire([{"span_id": "P1.s1"}, {"span_id": "P1.s2"}]), spans, GOALS,
    )
    proofs = result["facets"][0]["proof"]
    assert [p["evidence_id"] for p in proofs] == ["P1", "P1"]
    assert proofs[0]["quote"] != proofs[1]["quote"]


@pytest.mark.parametrize("proof", [
    [{"span_id": "P99"}],
    [{"span_id": "P1"}, {"span_id": "P1"}],
    [{"span_id": ["P1"]}],
    [{"span_id": "P1", "quote": "fabricated text"}],
    [{"evidence_id": "P1", "quote": "free-form quote is not a live fallback"}],
    [None],
])
def test_unknown_repeated_or_model_authored_proof_is_rejected(proof):
    _, spans = progress_spans(offered())
    with pytest.raises(LLMResponseError):
        materialize_proofs(wire(proof), spans, GOALS)


def test_single_sentence_has_no_alias_that_can_duplicate_the_same_proof():
    _, spans = progress_spans([{"evidence_id": "P1", "quote": "One original sentence."}])
    assert list(spans) == ["P1"]


def test_numbered_values_and_later_source_identity_are_not_invented():
    text = "We measured 0.25, not 0.7. Another observation is bounded."
    _, spans = progress_spans([{"evidence_id": "P2", "quote": text}])
    for key in spans:
        result = materialize_proofs(wire([{"span_id": key}]), spans, GOALS)
        proof = result["facets"][0]["proof"][0]
        assert proof["evidence_id"] == "P2" and proof["quote"] in text


@pytest.mark.parametrize("defect", ["unknown_id", "authored_quote", "wrong_type"])
def test_source_goal_is_also_selected_not_retyped_or_normalized(defect):
    _, spans = progress_spans(offered())
    raw = wire([])
    if defect == "unknown_id":
        raw["facets"][0]["goal_span_id"] = "S9"
    elif defect == "wrong_type":
        raw["facets"][0]["goal_span_id"] = ["S0"]
    else:
        raw["facets"][0].pop("goal_span_id")
        raw["facets"][0]["goal_quote"] = "Reworded plan is not a wire fallback."
    with pytest.raises(LLMResponseError):
        materialize_proofs(raw, spans, GOALS)


def test_goals_exclude_completed_measurements_and_do_not_repeat_parent_paragraph():
    plan = "Future work should characterize storage overhead."
    generation = "Making generation less sequential is another research goal of ours."
    completed = "We measured 29 batches and found the same training speed."
    text = completed + " " + plan + " " + generation
    assert future_goal_quotes(text) == [plan, generation]
    assert all(quote in text for quote in future_goal_quotes(text))


@pytest.mark.asyncio
async def test_implicit_goal_requires_manual_review_without_a_model_call():
    from app.llm.deepseek_responses import DeepSeekResponsesProvider

    provider = DeepSeekResponsesProvider(
        api_key="test-placeholder", model="test", base_url="https://example.test",
        timeout_seconds=1,
    )
    calls = []

    async def unexpected(payload):
        calls.append(payload)
        raise AssertionError("Ambiguous goal must not be sent")

    provider._post_response = unexpected
    with pytest.raises(LLMResponseError, match="未调用模型"):
        await provider.assess_future_work("Completed runtime measurements are reported.", [])
    assert not calls
