import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import checked_claims, unsupported_numeric_facts


def evidence(quote="The measured coefficients are 0.83 and 0.997."):
    return EvidenceAnchor(evidence_id="E1", document_id="d", page_number=1, quote=quote, score=1)


@pytest.mark.parametrize("labels", [("β1", "β2"), ("β₁", "β₂"), ("α3", "γ4"), ("𝛽₁", "𝛽₂")])
def test_variable_subscript_is_not_an_extra_numerical_assertion(labels):
    text = f"{labels[0]} = 0.83，{labels[1]} = 0.997。"
    accepted, rejected = checked_claims(
        [GeneratedClaim(text=text, evidence_ids=["E1"])], [evidence()]
    )
    assert rejected == 0
    assert accepted[0].text == text


@pytest.mark.parametrize(
    "text",
    [
        "β1 = 0.95，β2 = 0.997。",
        "β₁ = 0.83，β₂ = 0.99。",
        "𝛽₁ = 0.83，𝛽₂ = 9.97。",
        "这项实验使用 3 块 GPU，β1 = 0.83。",
    ],
)
def test_changed_values_and_real_counts_remain_rejected(text):
    assert unsupported_numeric_facts(text, [evidence()])


def test_variable_value_after_equals_sign_requires_evidence():
    assert unsupported_numeric_facts("β = 3。", [evidence()])
    assert not unsupported_numeric_facts("β = 3。", [evidence("The coefficient is 3.")])


def test_cjk_adjacent_number_is_still_checked():
    assert unsupported_numeric_facts("用了3块GPU。", [evidence()])
