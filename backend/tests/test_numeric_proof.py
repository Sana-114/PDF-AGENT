import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import checked_claims, unsupported_numeric_facts


def evidence(quote, evidence_id="E1"):
    return EvidenceAnchor(evidence_id=evidence_id, document_id="paper", page_number=8,
                          quote=quote, score=1)


@pytest.mark.parametrize("source,claim", [
    ("175B parameters", "模型具有1750亿参数"),
    ("175 billion parameters", "参数量为1750 亿"),
    ("1542M parameters", "15.42亿参数"),
    ("117M parameters", "1.17 亿参数"),
    ("1.3B parameters", "1300M parameters"),
])
def test_exact_quantity_conversion_is_supported(source, claim):
    assert unsupported_numeric_facts(claim, [evidence(source)]) == []


@pytest.mark.parametrize("claim", ["175亿参数", "1750M parameters", "1750参数", "1751亿参数"])
def test_wrong_units_or_values_are_still_rejected(claim):
    assert unsupported_numeric_facts(claim, [evidence("175B parameters")])


def test_unit_conversion_requires_the_claims_own_citation():
    claims, rejected = checked_claims([
        GeneratedClaim(text="1750 亿参数", evidence_ids=["E1"]),
        GeneratedClaim(text="1750 亿参数", evidence_ids=["E2"]),
    ], [evidence("117M", "E1"), evidence("175B", "E2")])
    assert rejected == 1 and claims[0].evidence_ids == ["E2"]


def test_source_backed_labelled_subtraction_is_accepted_without_rounding():
    text = "推导：28.4 - 26.36 = 2.04 BLEU。"
    assert unsupported_numeric_facts(text, [evidence("28.4; previous best 26.36")]) == []
    assert unsupported_numeric_facts(text.replace("2.04", "3.24"), [evidence("28.4; 26.36")])
    assert unsupported_numeric_facts(text.replace("2.04", "26.36"), [evidence("28.4; 26.36")])
    assert unsupported_numeric_facts("提升 2.04 BLEU", [evidence("28.4; 26.36")])


def test_subtraction_cannot_combine_unrelated_passages_or_missing_operands():
    text = "推导：28.4 - 26.36 = 2.04 BLEU。"
    assert unsupported_numeric_facts(text, [evidence("28.4"), evidence("26.36", "E2")])
    assert unsupported_numeric_facts(text, [evidence("28.4; 25.16")])


def test_numbers_in_chinese_prose_and_negative_values_are_not_silently_skipped():
    assert unsupported_numeric_facts("使用了99块GPU", [evidence("8 GPUs")])
    assert unsupported_numeric_facts("Value is -0.9", [evidence("0.9")])
    assert unsupported_numeric_facts("GPT-3 contains 175B", [evidence("175B")]) == []
    assert unsupported_numeric_facts("Adam uses 0.99.", [evidence("0.98.")])
