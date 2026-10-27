import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.grounded_text import checked_claims


def anchor(quote, evidence_id="E1", document_id="doc", title=None):
    return EvidenceAnchor(evidence_id=evidence_id, document_id=document_id,
                          document_title=title, page_number=4, quote=quote, score=1)


EQUIVALENCE = ("The smallest model is equivalent to the original GPT, and the second smallest "
               "equivalent to the largest model from BERT. Our largest model, which we call "
               "GPT-2, has over an order of magnitude more parameters than GPT.")


@pytest.mark.parametrize("text", [
    "GPT-2 论文指出其第二大模型等价于BERT的最大模型。",
    "The second largest model is equivalent to the largest model from BERT.",
    "第二大模型对应 BERT 最大模型。",
])
def test_contradicted_equivalence_ordinal_is_rejected_even_without_a_number(text):
    evidence = [anchor(EQUIVALENCE)]
    assert claim_semantic_conflicts(text, evidence) == ["ordinal_model_equivalence_mismatch"]
    accepted, rejected = checked_claims([GeneratedClaim(text=text, evidence_ids=["E1"])], evidence)
    assert not accepted and rejected == 1


@pytest.mark.parametrize("text", [
    "GPT-2 论文指出其第二小模型等价于BERT的最大模型。",
    "The second smallest model is equivalent to the largest model from BERT.",
    "GPT-2的最小模型对应原始GPT，GPT-2的最大模型比GPT大一个数量级。",
])
def test_correct_equivalence_and_unrelated_largest_statements_are_preserved(text):
    assert not claim_semantic_conflicts(text, [anchor(EQUIVALENCE)])


def test_equivalence_target_is_not_borrowed_from_a_different_comparison():
    evidence = [anchor("The second smallest model is equivalent to GPT-4.")]
    assert not claim_semantic_conflicts("第二大模型等价于BERT。", evidence)
    # Unknown relations require source review; this narrow rule is not proof of entailment.


def test_paper_attribution_uses_cited_document_identity_not_a_compared_model_name():
    identity = anchor("The last is the model we call GPT-3.", "E1", "gpt3")
    compared = anchor("GPT-3 operates differently; sizes are larger than those used for GPT-2.",
                      "E2", "gpt3")
    wrong = "GPT-2 论文提到其规模与 GPT-3 不同（该陈述出自 GPT-3 论文）。"
    good = "GPT-3论文说明其规模比GPT-2更大。"
    assert claim_semantic_conflicts(wrong, [compared], [identity, compared]) == [
        "paper_attribution_mismatch"
    ]
    assert not claim_semantic_conflicts(good, [compared], [identity, compared])
    claims, count = checked_claims([GeneratedClaim(text=wrong, evidence_ids=["E2"]),
                                   GeneratedClaim(text=good, evidence_ids=["E2"])],
                                  [identity, compared])
    assert count == 1 and [item.text for item in claims] == [good]


def test_unknown_or_ambiguous_source_identity_is_not_guessed():
    unknown = anchor("A comparison of GPT-2 and GPT-3.")
    assert not claim_semantic_conflicts("GPT-2论文指出两者不同。", [unknown])
    ambiguous = unknown.model_copy(update={"document_title": "GPT-2 versus GPT-3"})
    assert not claim_semantic_conflicts("GPT-2论文指出两者不同。", [ambiguous])


def test_named_paper_in_title_and_pdf_wrapped_ordinals():
    evidence = [anchor("the second smallest\nmodel is equivalent to BERT.", title="GPT-4")]
    assert "ordinal_model_equivalence_mismatch" in claim_semantic_conflicts(
        "GPT-4论文指出其第二大模型对应BERT。", evidence
    )
    assert "paper_attribution_mismatch" in claim_semantic_conflicts(
        "GPT-2论文指出其第二小模型对应BERT。", evidence
    )
