import pytest

from app.schemas.agent import EvidenceAnchor
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.grounded_text import unsupported_numeric_facts


def source(text):
    return EvidenceAnchor(evidence_id="E1", document_id="public", page_number=2, quote=text, score=1)


@pytest.mark.parametrize("text", ["Model(L=6,H=512,A=8,70M)", "Model (L=6, A=8, 70M)."])
def test_last_scaled_count_after_named_structural_tuple_is_not_a_grouped_head_count(text):
    assert not unsupported_numeric_facts(text, [source("L=6, H=512, A=8, Total Parameters=70M.")])


def test_ordinary_grouped_quantity_and_wrong_structural_values_still_fail():
    evidence = [source("L=6, H=512, A=8, Total Parameters=70M.")]
    assert unsupported_numeric_facts("The model has 8,070M parameters.", evidence)
    assert unsupported_numeric_facts("Model(L=6,A=9,70M)", evidence)
    assert unsupported_numeric_facts("Model(L=6,A=8,80M)", evidence)


def test_positionwise_feedforward_is_not_a_claim_of_normalization_position():
    text = ("原始 Transformer encoder 的每层含多头自注意力与逐位置前馈网络两个子层，"
            "并配有残差连接与层归一化。")
    evidence = [source("The encoder has self-attention and a position-wise feed-forward network. "
                       "We use residual connections followed by layer normalization.")]
    assert not claim_semantic_conflicts(text, evidence)
