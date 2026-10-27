import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import (
    audit_grounded_claims,
    checked_claims,
    unsupported_numeric_facts,
)


def anchor(quote, evidence_id="E1", document_id="paper"):
    return EvidenceAnchor(evidence_id=evidence_id, document_id=document_id,
                          page_number=1, quote=quote, score=1)


@pytest.mark.parametrize("text,source", [
    ("Evaluated on CIFAR-10/100.", "Evaluated on CIFAR-10 and CIFAR-100."),
    ("Dataset-7/19 are benchmarks.", "Dataset-7 and Dataset-19 are benchmarks."),
    ("Model-A-12 / 24 was evaluated.", "Model-A-12 and Model-A-24 were evaluated."),
    ("Dataset-７/１９ was used.", "Dataset-7 and Dataset-19 were used."),
])
def test_named_shorthand_needs_both_explicit_names_without_mutating_text(text, source):
    evidence = [anchor(source)]
    accepted, rejected = checked_claims([GeneratedClaim(text, ["E1"])], evidence)
    assert rejected == 0 and accepted[0].text == text
    assert evidence[0].quote == source


@pytest.mark.parametrize("text,source", [
    ("CIFAR-10/100 with 99 GPUs.", "CIFAR-10 and CIFAR-100 with 8 GPUs."),
    ("Dataset-7/19 reached 95%.", "Dataset-7 and Dataset-19 reached 90%."),
    ("Dataset-7/29 was used.", "Dataset-7 and Dataset-19 were used."),
    ("Dataset-7/19 was used.", "Dataset-7 was used."),
    ("Dataset-7/19 was used.", "Dataset-7 and Other-19 were used."),
    ("Dataset-7/19 was used.", "Dataset-7 and Dataset-190 were used."),
    ("Dataset-7/19 was used.", "Dataset-7 and Dataset-19M were used."),
    ("accuracy is 7/19.", "Dataset-7 and Dataset-19 were used."),
])
def test_unreported_variant_or_measurement_remains_rejected(text, source):
    assert unsupported_numeric_facts(text, [anchor(source)])


def test_shorthand_does_not_borrow_names_from_uncited_or_different_passages():
    evidence = [anchor("Dataset-7 was used."),
                anchor("Dataset-7 and Dataset-19 were used.", "E2", "another")]
    accepted, rejected = checked_claims([
        GeneratedClaim("Dataset-7/19 was used.", ["E1"]),
    ], evidence)
    assert rejected == 1 and accepted == []
    assert unsupported_numeric_facts("Dataset-7/19 was used.",
        [anchor("Dataset-7."), anchor("Dataset-19.", "E2")])


def test_audit_keeps_original_input_count_sources_and_specific_rejection_reason():
    evidence = [anchor("Dataset-7 and Dataset-19 with 8 GPUs.")]
    claims = [GeneratedClaim("Dataset-7/19 uses 8 GPUs.", ["E1", "E1"]),
              GeneratedClaim("Uses 99 GPUs.", ["E1"]),
              GeneratedClaim("A finding.", ["E9"]),
              GeneratedClaim("", ["E1"])]
    accepted, audit = audit_grounded_claims(claims, evidence)
    assert audit.input_claim_count == 4 and audit.accepted_claim_count == 1
    assert audit.document_claim_counts == {"paper": 3}
    assert [item.claim_index for item in audit.rejections] == [1, 2, 3]
    assert audit.rejections[0].reasons == ["unsupported_numeric_fact:99"]
    assert audit.rejections[1].reasons == ["missing_allowed_citation"]
    assert audit.rejections[2].reasons == ["empty_statement"]
    assert checked_claims(claims, evidence) == (accepted, 3)


def test_audit_counts_a_cross_paper_claim_once_for_each_actual_cited_document():
    evidence = [anchor("Method A.", "E1", "one"), anchor("Method B.", "E2", "two")]
    _, audit = audit_grounded_claims([GeneratedClaim("Method comparison.",
        ["E1", "E1", "E2"])], evidence)
    assert audit.document_claim_counts == {"one": 1, "two": 1}
