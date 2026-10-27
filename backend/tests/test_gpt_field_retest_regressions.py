"""Synthetic counterexamples for holes discovered by the two-question real retest."""

import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.answer_completeness import (
    affirmative_model_values,
    affirmative_relations,
    contract_prompt,
    missing_fields,
    requested_models,
)
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.grounded_text import checked_claims
from scripts.review_official_first_round import check_reviewed_fact


def anchor(quote, key="E1"):
    return EvidenceAnchor(evidence_id=key, document_id="later-paper", page_number=4,
                          quote=quote, score=0.8)


@pytest.mark.parametrize("relation", ["等同于", "相当于", "equates to"])
def test_equivalence_synonyms_cannot_bypass_the_ordinal_conflict_guard(relation):
    text = f"第二大模型{relation} BERT 最大模型。"
    sources = [anchor("The second smallest model is equivalent to the largest BERT model.")]
    assert "ordinal_model_equivalence_mismatch" in claim_semantic_conflicts(text, sources)
    assert checked_claims([GeneratedClaim(text=text, evidence_ids=["E1"])], sources) == ([], 1)
    assert not claim_semantic_conflicts(f"第二小模型{relation} BERT 最大模型。", sources)


def test_affirmative_parameter_size_list_is_bound_to_the_named_model_family():
    text = "GPT-2 的模型参数量分别为60M、250M和800M，其中最大的是800M。"
    assert affirmative_model_values(text, "model_parameters") == {"GPT-2": {
        "quantity:60000000", "quantity:250000000", "quantity:800000000"}}
    assert missing_fields("GPT-2 参数量？", [AnswerClaim(text=text, evidence_ids=["E1"])],
                          [anchor("Parameters Layers d_model\n60M 4 256; 800M 16 512")]) == []


@pytest.mark.parametrize("text", [
    "原始 GPT 的参数量为60M（来自后续论文，非原论文直接报告）。",
    "original GPT has 60M parameters, inferred from the later paper.",
    "由后续论文说明可以推断：60M 对应原始 GPT 的参数量，非原论文直接报告。",
])
def test_explicit_original_gpt_alias_and_source_qualified_reverse_assignment(text):
    assert affirmative_model_values(text, "model_parameters").get("GPT-1") == {
        "quantity:60000000"}
    assert missing_fields("GPT-1 参数量？", [AnswerClaim(text=text, evidence_ids=["E1"])],
                          [anchor("Parameters Layers d_model\n60M 4 256")]) == []
    assert requested_models("原始 GPT 和 GPT-2 的参数量？") == ["GPT-1", "GPT-2"]


@pytest.mark.parametrize("text", [
    "无法确认60M对应原始 GPT 的参数量。",
    "60M 可能对应原始 GPT 的参数量。",
    "原始 GPT 的参数量分别为60M、800M，但这只是猜测。",
])
def test_alias_or_reverse_assignment_must_not_bypass_refusal_or_speculation(text):
    assert not affirmative_model_values(text, "model_parameters")


def test_original_alias_does_not_relabel_an_explicit_numbered_generation():
    assert requested_models("原始 GPT-2 和 original GPT-3 参数量？") == ["GPT-2", "GPT-3"]
    assert affirmative_model_values("原始 GPT-2 参数量为60M", "model_parameters") == {
        "GPT-2": {"quantity:60000000"}}


def test_lists_of_other_scaled_quantities_are_not_parameter_assignments():
    assert not affirmative_model_values(
        "GPT-2 的训练 token 数分别为60M、250M和800M；参数量另见文档。",
        "model_parameters")


def test_fact_review_accepts_only_an_explicit_alias_value_not_a_refusal():
    fact = {"source_filename": "later.pdf", "source_pages": [4],
            "source_text_contains_any": ["60M"], "claim_contains_any": ["60M"],
            "claim_contains_any_groups": [["GPT-1", "原始 GPT"]]}
    response = type("Response", (), {
        "claims": [AnswerClaim(text="原始 GPT 的参数量为60M，依据后续论文推断。",
                               evidence_ids=["E1"])],
        "evidence": [anchor("Parameters Layers d_model\n60M 4 256")],
    })()
    assert check_reviewed_fact(fact, response, {"later.pdf": "later-paper"})
    response.claims[0].text = "原始 GPT 参数量未知；60M不是可确认的结论。"
    assert not check_reviewed_fact(fact, response, {"later.pdf": "later-paper"})


def test_context_contract_does_not_require_the_literal_maximum_label_or_allow_training_only():
    prompt = contract_prompt("GPT-1 到 GPT-3 最大上下文窗口？")
    assert "literal word 'maximum'" in prompt
    assert "training sequence length alone" in prompt
    assert "one standalone" in prompt


@pytest.mark.parametrize("suffix", ["但这只是猜测。", "原始 GPT 的参数量未知。"])
def test_reverse_assignment_is_retracted_by_a_later_same_subject_uncertainty(suffix):
    assert affirmative_model_values("60M对应原始 GPT 的参数量；" + suffix,
                                    "model_parameters") == {}


def test_uncertainty_for_another_named_model_does_not_retract_a_supported_original_alias():
    assert affirmative_model_values(
        "60M对应原始 GPT 的参数量；GPT-2 的参数量未知。", "model_parameters",
    ) == {"GPT-1": {"quantity:60000000"}}


@pytest.mark.parametrize("kind, text", [
    ("model_parameters", "GPT-2 参数量为60M；GPT-2 参数量未知。"),
    ("model_context", "GPT-2 上下文为1536 tokens；GPT-2 的窗口无法确认。"),
])
def test_repeating_the_same_subject_cannot_hide_an_explicit_retraction(kind, text):
    assert affirmative_model_values(text, kind) == {}


@pytest.mark.parametrize("text", [
    "连续640 token训练序列可作为 GPT-1 上下文长度的推断。",
    "GPT-1 的训练序列长度可以作为上下文窗口的推断。",
    "GPT-1 training sequence length can be used as its context window.",
])
def test_training_only_window_inference_is_not_published_even_if_all_numbers_exist(text):
    sources = [anchor("We train on contiguous sequences of 640 tokens.")]
    assert "training_sequence_only_context_inference" in claim_semantic_conflicts(text, sources)
    assert checked_claims([GeneratedClaim(text=text, evidence_ids=["E1"])], sources) == ([], 1)


@pytest.mark.parametrize("text, quote", [
    ("GPT-1 训练序列是640 tokens，不能据此推断上下文窗口。",
     "We train on contiguous sequences of 640 tokens."),
    ("GPT-1 training length cannot be used as its context window.",
     "We train on contiguous sequences of 640 tokens."),
    ("完整1280-token序列可作为 GPT-1 上下文长度的推断。",
     "We train on sequences of the full n_ctx = 1280 token context window."),
])
def test_training_facts_refusals_and_an_explicit_full_window_definition_are_preserved(text, quote):
    assert not claim_semantic_conflicts(text, [anchor(quote)])


@pytest.mark.parametrize("text", [
    "GPT-1 的参数量为60M（依据后续论文推断）；GPT-1 论文未提供其参数量数值。",
    "GPT-1 的参数量为60M（依据后续论文推断）；而非 GPT-1 论文直接报告其自身参数量"
    "（GPT-1 论文未提供其参数量数值）。",
    "60M对应原始 GPT 的参数量；GPT-1 论文未直接给出参数量。",
    "GPT-1 has 60M parameters, inferred from the later paper; "
    "the GPT-1 paper does not directly report its parameter count.",
    "GPT-1 上下文为640 tokens（依据后续配置对比推断）；GPT-1 论文未直接报告其上下文窗口。",
])
def test_paper_reporting_limitation_does_not_retract_a_qualified_assignment(text):
    kind = "model_context" if "上下文" in text else "model_parameters"
    expected = "640" if kind == "model_context" else "quantity:60000000"
    assert affirmative_model_values(text, kind).get("GPT-1") == {expected}


@pytest.mark.parametrize("text", [
    "GPT-1 参数量为60M；非原论文直接报告，数值只是猜测。",
    "GPT-1 参数量为60M；GPT-1 论文未提供其参数量数值，数值未知。",
    "60M对应原始 GPT 的参数量；非原论文直接报告，这只是猜测。",
    "GPT-1 has 60M parameters; not reported in the original paper, "
    "and the value is unknown.",
    "GPT-1 参数量为60M；GPT-1 参数量未提供。",
    "GPT-1 参数量为60M；GPT-1 论文未提供可确认的参数结论，这只是猜测。",
])
def test_paper_provenance_must_not_hide_a_real_refusal_or_retraction(text):
    assert affirmative_model_values(text, "model_parameters") == {}


@pytest.mark.parametrize("assignment", [
    "GPT-1 的参数量推断为60M。",
    "GPT-1 的参数量可推断为60M。",
    "GPT-1 parameter count is inferred to be 60M.",
])
def test_explicit_inference_verb_is_an_assignment_not_an_unlabelled_quantity(assignment):
    assert affirmative_model_values(assignment, "model_parameters") == {
        "GPT-1": {"quantity:60000000"}}
    assert missing_fields("GPT-1 参数量？", [AnswerClaim(text=assignment, evidence_ids=["E1"])],
                          [anchor("Parameters Layers d_model\n60M 4 256")]) == []


@pytest.mark.parametrize("text", [
    "GPT-1 的参数量可能推断为60M，这只是猜测。",
    "GPT-1 的参数量推断为60M；GPT-1 的参数量无法确认。",
])
def test_an_inference_verb_does_not_override_speculation_or_a_later_retraction(text):
    assert affirmative_model_values(text, "model_parameters") == {}


def test_inference_assignment_still_needs_a_cited_count_and_provenance_is_not_an_answer():
    claim = AnswerClaim(text="GPT-1 的参数量推断为60M。", evidence_ids=["E1"])
    assert missing_fields("GPT-1 参数量？", [claim], [anchor("No numeric count here.")]) == [
        "GPT-1 参数量"]
    assert not affirmative_model_values("GPT-1 论文未提供其参数量数值。", "model_parameters")


def test_relation_provenance_cannot_hide_speculation_in_the_same_clause():
    assert affirmative_relations(
        "BERT 继承编码器且抛弃解码器；非原论文直接报告，这只是猜测。") == (False, False)
