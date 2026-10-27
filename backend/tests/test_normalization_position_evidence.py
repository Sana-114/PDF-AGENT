import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.grounded_text import checked_claims


def source(text, key="E1"):
    return EvidenceAnchor(evidence_id=key, document_id="public", page_number=5, quote=text, score=1)


@pytest.mark.parametrize("claim", [
    "GPT-1 未做此调整，其特征是 layernorm 在整个模型中广泛使用。",
    "GPT-1 的 Layer Normalization 在原始 Transformer 位置使用，并未描述上述调整。",
    "Layer normalization was moved to the input of each sub-block.",
    "LN 放在残差层之前。",
])
def test_general_norm_usage_does_not_prove_a_position_or_unchanged_comparison(claim):
    evidence = [source("Since layernorm is used extensively throughout the model, initialization suffices.")]
    assert "normalization_position_missing_from_cited_passage" in claim_semantic_conflicts(claim, evidence)
    assert checked_claims([GeneratedClaim(claim, ["E1"])], evidence)[1] == 1


def test_unrelated_input_sentence_and_uncited_position_are_not_support():
    evidence = [source("The input uses embeddings. Layer normalization is used extensively."),
                source("Layer normalization was moved to the input of each sub-block.", "E2")]
    claims, rejected = checked_claims([GeneratedClaim("LN 移动到输入处。", ["E1"])], evidence)
    assert not claims and rejected == 1


@pytest.mark.parametrize("quote,claim", [
    ("Layer normalization was moved to the input of each sub-block.", "LN 移动到输入处。"),
    ("Layer normalization\nwas moved to the input of each sub-block.", "LN 移动到输入处。"),
    ("An additional layer normalization was added after the final self-attention block.",
     "在最后一个注意力块之后增加 LN。"),
    ("Layernorm is used extensively throughout the model.", "模型广泛使用 layernorm。"),
])
def test_direct_positions_or_plain_usage_are_preserved(quote, claim):
    assert not claim_semantic_conflicts(claim, [source(quote)])


def test_target_and_final_position_do_not_prove_the_starting_subblock_position():
    evidence = [source("Layer normalization was moved to the input of each sub-block, "
                       "and an additional layer normalization was added after the final self-attention block.")]
    assert claim_semantic_conflicts("GPT-2 将 Layer Normalization 从每个子模块之后移到了输入端。", evidence) == [
        "normalization_origin_not_supported_by_cited_passage"]
    evidence = [source("Layer normalization was moved from after each sub-block to its input.")]
    assert not claim_semantic_conflicts("LN 从子模块之后移到输入端。", evidence)
