import pytest

from app.llm.review_limits import (
    filter_review_publication,
    ungrounded_compute_nonreduction,
    unqualified_compute_comparator,
    unqualified_gradient_stability,
    unqualified_human_preference,
)
from app.schemas.agent import AnswerClaim, EvidenceAnchor


def anchor(quote, source="text"):
    return EvidenceAnchor(evidence_id="R1", document_id="paper", page_number=1,
                          quote=quote, source_type=source, score=0.8)


@pytest.mark.parametrize("text", ["用IO分块加速，而非降低FLOP。",
                                  "减少读写，而不是减少FLOPs。",
                                  "It is faster without reducing FLOPs."])
def test_negative_compute_comparison_cannot_use_only_io_result(text):
    io = anchor("Tiling reduces the number of memory reads/writes.")
    assert ungrounded_compute_nonreduction(text, [io])
    compute = anchor("The method has a higher FLOP count due to recomputation.")
    assert not ungrounded_compute_nonreduction(text, [io, compute])
    assert ungrounded_compute_nonreduction(text, [io, compute.model_copy(
        update={"source_type": "reference"})])
    assert ungrounded_compute_nonreduction(text, [io, anchor("Recomputation is used.")])


def test_plain_io_statement_does_not_require_a_flop_comparison():
    assert not ungrounded_compute_nonreduction("分块减少HBM读写。", [anchor("Tiling reduces IO.")])


@pytest.mark.parametrize("text,held", [
    ("Pre-LN梯度表现良好，可以取消warm-up。", True),
    ("Post-LN在初始化时有大梯度；Pre-LN梯度表现良好。", True),
    ("Pre-LN gradients are well-behaved.", True),
    ("该文理论表明Pre-LN在初始化时梯度表现良好。", False),
    ("Pre-LN gradients are well-behaved at initialization.", False),
    ("该文实验移除了warm-up。", False),
])
def test_initialization_is_local_to_each_proposition(text, held):
    source = anchor("The gradients are well-behaved at initialization.")
    assert unqualified_gradient_stability(text, [source]) is held
    assert not unqualified_gradient_stability(text, [anchor("Experimental training converges.")])
    assert not unqualified_gradient_stability(text, [source.model_copy(
        update={"source_type": "reference"})])


@pytest.mark.parametrize("text,held", [
    ("InstructGPT比GPT-3更受偏好。", True),
    ("在人类评估中，InstructGPT比GPT-3更受偏好。", True),
    ("在文中提示分布上，InstructGPT比GPT-3更受偏好。", True),
    ("在作者提示分布上，A被偏好于B。", True),
    ("在文中提示分布的人类评估中，InstructGPT比GPT-3更受偏好。", False),
    ("In human evaluations on their prompt distribution, A is preferred to B.", False),
    ("人类反馈用于对齐。", False),
])
def test_preference_retains_evaluator_and_reported_prompt_scope(text, held):
    source = anchor("In human evaluations on our prompt distribution, outputs from A "
                    "are preferred to outputs from B.")
    assert unqualified_human_preference(text, [source]) is held
    assert not unqualified_human_preference(text, [anchor("A is evaluated on general tasks.")])


def test_publication_withholds_instead_of_rewriting_or_using_uncited_pool():
    evidence = [anchor("Tiling reduces IO.")]
    text = "该方法通过IO分块，而非降低FLOP。"
    claim = AnswerClaim(text=text, evidence_ids=["R1"])
    evidence.append(anchor("The FLOP count is higher.").model_copy(update={"evidence_id": "R2"}))
    kept, warnings = filter_review_publication([claim], evidence)
    assert kept == [] and len(warnings) == 1
    assert claim.text == text and claim.evidence_ids == ["R1"]


def test_higher_compute_result_keeps_actual_baseline_and_backward_scope():
    source = anchor("FlashAttention has higher FLOP count compared to standard attention "
                    "(due to recomputation in the backward pass).")
    assert unqualified_compute_comparator("其FLOP数反而更高。", [source])
    assert unqualified_compute_comparator("比标准注意力的FLOP数更高。", [source])
    assert not unqualified_compute_comparator(
        "反向传播重计算使其FLOP数比标准注意力更高。", [source])
    assert not unqualified_compute_comparator("分块减少IO。", [source])


def test_saved_review_provider_cannot_bypass_readable_paper_attribution():
    evidence = [anchor("An original abstract.")]
    bad = AnswerClaim(text="P1 的初步实验显示卷积效果有待研究。", evidence_ids=["R1"])
    good = AnswerClaim(text="P1 + P2 是一个公式符号。", evidence_ids=["R1"])
    kept, warnings = filter_review_publication([bad, good], evidence)
    assert kept == [good] and "内部P编号" in warnings[0]
