import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import checked_claims

SOURCE = (
    "With mean field theory, at initialization, for the Post-LN model, the expected "
    "gradients of the parameters near the output layer are large. Using a large "
    "learning rate on those gradients makes training unstable. In Pre-LN the "
    "gradients are well-behaved at initialization."
)


@pytest.mark.parametrize("text", [
    "Post-LN 原位梯度大。",
    "平均场理论指出Post-LN原位梯度大。",
    "Post-LN gradients are large.",
    "Post-LN 在初始化时梯度很大。",
    "Post-LN 输出层附近的参数期望梯度大。",
    "Post-LN 输出层附近参数的期望梯度大，初始学习率很高。",
    "Post-LN 在初始化时隐藏层参数的期望梯度大。",
    "Post-LN 靠近输出层参数的期望梯度大。",
])
def test_large_gradient_claim_preserves_explicit_initialization_and_parameter_scope(text):
    source = EvidenceAnchor(evidence_id="E1", document_id="paper", page_number=1,
                            quote=SOURCE, score=1)
    accepted, rejected = checked_claims([GeneratedClaim(text, ["E1"])], [source])
    assert accepted == [] and rejected == 1


@pytest.mark.parametrize("text", [
    "Post-LN 在初始化时，输出层附近参数的期望梯度较大。",
    "Post-LN输出层附近初始期望梯度大。",
    "原始Post-LN Transformer在初始化时、靠近输出层参数的期望梯度很大。",
    "At initialization, Post-LN has large expected gradients near the output layer.",
    "Pre-LN gradients are well-behaved at initialization.",
    "Layer normalization computes single-case statistics within a layer.",
    "Post-LN does not universally have large gradients.",
])
def test_qualified_or_distinct_method_facts_remain_publishable(text):
    source = EvidenceAnchor(evidence_id="E1", document_id="paper", page_number=1,
                            quote=SOURCE, score=1)
    accepted, rejected = checked_claims([GeneratedClaim(text, ["E1"])], [source])
    assert rejected == 0 and accepted[0].text == text


def test_a_reference_title_or_other_uncited_paper_cannot_trigger_the_scope_rule():
    primary = EvidenceAnchor(evidence_id="E1", document_id="one", page_number=1,
        quote="In this experiment Post-LN has large gradients.", score=1)
    reference = primary.model_copy(update={"evidence_id": "E2", "document_id": "two",
        "quote": SOURCE, "source_type": "reference"})
    accepted, rejected = checked_claims([GeneratedClaim("Post-LN has large gradients.",
        ["E1"])], [primary, reference])
    assert rejected == 0 and len(accepted) == 1
