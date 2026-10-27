"""Unit conversion is generic; evidence and magnitude restrictions stay enforced."""

import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import checked_claims, normalise_fact, unsupported_numeric_facts


def source(text):
    return EvidenceAnchor(evidence_id="E1", document_id="doc", page_number=1,
                          quote=text, score=1, recall_mode="hybrid", retrieval_mode="reranked")


@pytest.mark.parametrize("a,b", [
    ("2.5 trillion", "2.5万亿"),
    ("0.25T", "250 billion"),
    ("1,500 billion", "1.5万亿"),
    ("1e-1 trillion", "100B"),
])
def test_trillion_equivalence_preserves_exact_quantity(a, b):
    assert normalise_fact(a) == normalise_fact(b)


def test_chinese_training_corpus_claim_supported_by_english_trillion_source():
    evidence = [source("The pre-training data increased from 2.5 trillion to 3.5 trillion tokens.")]
    claim = GeneratedClaim(text="预训练语料从2.5万亿增至3.5万亿 token。", evidence_ids=["E1"])
    accepted, rejected = checked_claims([claim], evidence)
    assert len(accepted) == 1 and rejected == 0


@pytest.mark.parametrize("claim", [
    "使用2.5亿token。", "使用3.5万亿token。", "使用25万亿token。", "使用2.5% token。",
])
def test_wrong_magnitude_value_or_percentage_still_rejected(claim):
    assert unsupported_numeric_facts(claim, [source("Trained on 2.5 trillion tokens.")])


def test_trillion_alias_never_substitutes_for_missing_citation():
    accepted, rejected = checked_claims([
        GeneratedClaim(text="使用2.5万亿token。", evidence_ids=["FAKE"])
    ], [source("Trained on 2.5 trillion tokens.")])
    assert not accepted and rejected == 1
