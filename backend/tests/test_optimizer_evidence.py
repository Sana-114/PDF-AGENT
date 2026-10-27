import asyncio

import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import GeneratedClaim
from app.schemas.agent import AskRequest, EvidenceAnchor
from app.services.grounded_text import checked_claims
from app.services.optimizer_evidence import explicit_beta_sources, missing_optimizer_beta_claims
from scripts.replay_official_failure_repairs import SavedProvider, SavedRegistry


def source(quote="We used Adam with β1 = 0.61, β2 = 0.73.", key="E1", document="public"):
    return EvidenceAnchor(evidence_id=key, document_id=document, page_number=4, quote=quote, score=1)


@pytest.mark.parametrize("label", ["β2", "β₂", "beta_2", r"\beta 2", r"\beta_{2}"])
def test_projects_arbitrary_explicit_values_not_paper_gold(label):
    evidence = [source(f"We used Adam with {label} = 0.73.")]
    result = missing_optimizer_beta_claims("Adam 的 β2 是否为 0.61？", evidence, [])
    assert len(result) == 1 and "β2 = 0.73" in result[0].text
    assert result[0].evidence_ids == ["E1"]


@pytest.mark.parametrize("quote", [
    "We used Adam with β2 = 0.73 or 0.61.",
    "We used Adam with β2 = 0.73-0.81.",
    "We used Adam with β2 = 0.73 to 0.81.",
    "We used Adam with β2 = 0.73 ± 0.02.",
    "We used Adam with β2 = 0.73, 0.61.",
    "If using Adam with β2 = 0.73, the loss decreases.",
    "The default Adam β2 = 0.73.",
    "We did not use Adam with β2 = 0.73.",
    "We used Adam with β2 = -0.73.",
    "We used Adam with β2 = 1.73.",
    "We used Adam with β2 = 0.73.1.",
    "We used SGD with β2 = 0.73.",
])
def test_ambiguous_or_non_adam_values_do_not_create_an_answer(quote):
    assert not missing_optimizer_beta_claims("Adam β2=?", [source(quote)], [])


def test_conflicts_across_passages_and_documents_abstain():
    for second in [source("Adam β2 = 0.61.", "E2"),
                   source("Adam β2 = 0.73.", "E2", "other")]:
        assert not missing_optimizer_beta_claims("Adam β2=?", [source(), second], [])


def test_reference_or_unrequested_field_is_not_projected():
    assert not explicit_beta_sources([source().model_copy(update={"source_type": "reference"})])
    assert not missing_optimizer_beta_claims("训练学习率是多少？", [source()], [])
    result = missing_optimizer_beta_claims("Adam β1=?", [source()], [])
    assert len(result) == 1 and "β1 = 0.61" in result[0].text and "β2" not in result[0].text


def test_known_beta_field_swap_is_rejected_despite_both_numbers_in_source():
    good = GeneratedClaim(text="Adam β1 = 0.61，β2 = 0.73。", evidence_ids=["E1"])
    wrong = GeneratedClaim(text="Adam β1 = 0.73，β2 = 0.61。", evidence_ids=["E1"])
    accepted, rejected = checked_claims([good, wrong], [source()])
    assert rejected == 1 and accepted[0].text == good.text
    assert not missing_optimizer_beta_claims("Adam β1 / β2=?", [source()], accepted)


@pytest.mark.parametrize("raw_text", [
    "β2 不是 0.87，论文中设定的是 0.73。",  # Deliberately not the paired-number grammar.
    "Adam 的 β2 = 0.61。",
])
def test_harness_preserves_rejection_and_projects_direct_source_when_needed(raw_text):
    response = asyncio.run(ResearchAgent(None, registry=SavedRegistry([source()]),
        provider=SavedProvider("saved", {"answer": "", "claims": [
            {"text": raw_text, "evidence_ids": ["E1"]}]})).ask(
        AskRequest(question="Adam β2 是否为 0.87？", document_ids=["public"])))
    assert not response.insufficient_evidence
    assert any("β2 = 0.73" in claim.text for claim in response.claims)
    assert all(claim.text != raw_text for claim in response.claims)
    assert any(step.skill == "project_explicit_optimizer_fields" for step in response.trace)


def test_multi_document_request_does_not_project_a_single_document_default():
    response = asyncio.run(ResearchAgent(None, registry=SavedRegistry([source()]),
        provider=SavedProvider("saved", {"answer": "", "claims": []})).ask(
        AskRequest(question="Adam β2=?", document_ids=["public", "other"])))
    assert response.insufficient_evidence and not response.claims
