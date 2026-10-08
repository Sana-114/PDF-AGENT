from types import SimpleNamespace

import pytest

from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from scripts.evaluate_real_pdf_comparison import (
    RecordingProvider,
    _check_case,
    _check_cross_document_fact,
    _check_fact,
)


@pytest.mark.asyncio
async def test_recording_provider_keeps_initial_and_supplemental_claims() -> None:
    class StubProvider:
        name = "deepseek"
        model = "deepseek-flash"
        supports_translation = True

        async def generate_grounded_answer(self, question, evidence):
            return GeneratedAnswer(
                answer=question,
                claims=[GeneratedClaim(text=question, evidence_ids=["E1"])],
            )

    provider = RecordingProvider(StubProvider())
    await provider.generate_grounded_answer("initial", [_source_anchor()])
    await provider.generate_grounded_answer("supplement", [_source_anchor()])

    assert [claim.text for claim in provider.generated_claims] == ["initial", "supplement"]


def _source_anchor(mode: str = "reranked") -> EvidenceAnchor:
    return EvidenceAnchor(
        evidence_id="E1",
        document_id="bert-document",
        page_number=4,
        block_ids=["p4-b6"],
        bbox=[72.0, 65.0, 290.0, 455.0],
        quote="BERT uses a masked LM to train bidirectional representations.",
        retrieval_mode=mode,
        score=0.9,
    )


def _comparison_anchor(
    evidence_id: str,
    document_id: str,
    page_number: int,
    quote: str,
) -> EvidenceAnchor:
    return EvidenceAnchor(
        evidence_id=evidence_id,
        document_id=document_id,
        page_number=page_number,
        block_ids=[f"p{page_number}-b1"],
        bbox=[72.0, 65.0, 290.0, 455.0],
        quote=quote,
        retrieval_mode="reranked",
        score=0.9,
    )


def _fact() -> dict:
    return {
        "fact_id": "masked-lm",
        "claim_contains_any": ["masked LM"],
        "source_filename": "bert.pdf",
        "source_text_contains_any": ["masked LM"],
        "source_pages": [4],
    }


def test_claim_fact_requires_the_cited_source_page() -> None:
    response = SimpleNamespace(
        evidence=[_source_anchor()],
        claims=[SimpleNamespace(text="BERT uses masked LM.", evidence_ids=["E1"])],
    )

    assert _check_fact(_fact(), response, {"bert.pdf": "bert-document"})
    assert not _check_fact(_fact(), response, {"bert.pdf": "other-document"})


def test_claim_fact_can_require_all_context_terms() -> None:
    fact = {**_fact(), "claim_contains_all": ["BERT", "train"]}
    response = SimpleNamespace(
        evidence=[_source_anchor()],
        claims=[SimpleNamespace(text="BERT uses masked LM.", evidence_ids=["E1"])],
    )

    assert not _check_fact(fact, response, {"bert.pdf": "bert-document"})
    response.claims[0].text = "BERT uses masked LM for training."
    assert _check_fact(fact, response, {"bert.pdf": "bert-document"})


def test_cross_document_fact_requires_both_sources_on_one_claim() -> None:
    transformer = _comparison_anchor(
        "E1",
        "transformer-document",
        2,
        "We employ a residual connection followed by layer normalization: "
        "LayerNorm(x + Sublayer(x)).",
    )
    gpt2 = _comparison_anchor(
        "E2",
        "gpt2-document",
        4,
        "Layer normalization was moved to the input of each sub-block.",
    )
    fact = {
        "fact_id": "two-source-layernorm",
        "claim_contains_any_groups": [
            ["Transformer"],
            ["GPT-2"],
            ["layer normalization", "LayerNorm"],
        ],
        "sources": [
            {
                "source_filename": "transformer.pdf",
                "source_text_contains_any": ["LayerNorm(x + Sublayer(x))"],
                "source_pages": [2],
            },
            {
                "source_filename": "gpt2.pdf",
                "source_text_contains_any": ["input of each sub-block"],
                "source_pages": [4],
            },
        ],
    }
    response = SimpleNamespace(
        evidence=[transformer, gpt2],
        claims=[
            SimpleNamespace(
                text=(
                    "The original Transformer applies layer normalization after the residual "
                    "addition, whereas GPT-2 moves it to each sub-block input."
                ),
                evidence_ids=["E1", "E2"],
            )
        ],
    )
    document_ids = {
        "transformer.pdf": "transformer-document",
        "gpt2.pdf": "gpt2-document",
    }

    assert _check_cross_document_fact(fact, response, document_ids)
    response.claims[0].evidence_ids = ["E1"]
    assert not _check_cross_document_fact(fact, response, document_ids)


def test_cross_document_fact_does_not_combine_separate_claim_citations() -> None:
    response = SimpleNamespace(
        evidence=[
            _comparison_anchor(
                "E1",
                "transformer-document",
                2,
                "LayerNorm(x + Sublayer(x)).",
            ),
            _comparison_anchor(
                "E2",
                "gpt2-document",
                4,
                "Layer normalization was moved to the input of each sub-block.",
            ),
        ],
        claims=[
            SimpleNamespace(
                text="Transformer uses LayerNorm after its residual addition.",
                evidence_ids=["E1"],
            ),
            SimpleNamespace(
                text="GPT-2 moves layer normalization to the sub-block input.",
                evidence_ids=["E2"],
            ),
        ],
    )
    fact = {
        "fact_id": "two-source-layernorm",
        "claim_contains_any_groups": [["Transformer"], ["GPT-2"], ["LayerNorm"]],
        "sources": [
            {
                "source_filename": "transformer.pdf",
                "source_text_contains_any": ["LayerNorm(x + Sublayer(x))"],
                "source_pages": [2],
            },
            {
                "source_filename": "gpt2.pdf",
                "source_text_contains_any": ["input of each sub-block"],
                "source_pages": [4],
            },
        ],
    }

    assert not _check_cross_document_fact(
        fact,
        response,
        {"transformer.pdf": "transformer-document", "gpt2.pdf": "gpt2-document"},
    )


def test_live_comparison_gate_rejects_hybrid_fallback() -> None:
    response = SimpleNamespace(
        provider="deepseek",
        model="deepseek-flash",
        insufficient_evidence=False,
        audit=SimpleNamespace(
            cross_document_claim_count=0,
            referenced_document_count=1,
        ),
        evidence=[_source_anchor(mode="hybrid")],
        claims=[SimpleNamespace(text="BERT uses masked LM.", evidence_ids=["E1"])],
        trace=[SimpleNamespace(skill="llm.compare_documents", status="ok")],
    )
    case = {"min_cross_document_claims": 0, "required_claim_facts": [_fact()]}

    result = _check_case(
        case,
        response,
        {"bert.pdf": "bert-document"},
        SimpleNamespace(model="deepseek-flash"),
    )

    assert result["facts"] == {"masked-lm": True}
    assert result["failures"] == ["retrieval_bypassed_reranker"]


def test_live_comparison_gate_accepts_explicit_refusal() -> None:
    response = SimpleNamespace(
        provider="deepseek",
        model="deepseek-flash",
        insufficient_evidence=True,
        audit=SimpleNamespace(cross_document_claim_count=0, referenced_document_count=0),
        evidence=[_source_anchor()],
        claims=[],
        trace=[SimpleNamespace(skill="llm.compare_documents", status="ok")],
    )
    case = {"must_refuse": True, "required_claim_facts": []}

    result = _check_case(
        case,
        response,
        {"bert.pdf": "bert-document", "transformer.pdf": "transformer-document"},
        SimpleNamespace(model="deepseek-flash"),
    )

    assert result["passed"] is True


def test_live_comparison_gate_rejects_refusal_without_bge_evidence() -> None:
    response = SimpleNamespace(
        provider="deepseek",
        model="deepseek-flash",
        insufficient_evidence=True,
        audit=SimpleNamespace(cross_document_claim_count=0, referenced_document_count=0),
        evidence=[],
        claims=[],
        trace=[SimpleNamespace(skill="llm.compare_documents", status="ok")],
    )
    case = {"must_refuse": True, "required_claim_facts": []}

    result = _check_case(
        case,
        response,
        {"bert.pdf": "bert-document"},
        SimpleNamespace(model="deepseek-flash"),
    )

    assert result["failures"] == ["retrieval_bypassed_reranker"]


def test_refusal_allows_grounded_context_but_rejects_target_quantity() -> None:
    response = SimpleNamespace(
        provider="deepseek",
        model="deepseek-flash",
        insufficient_evidence=True,
        audit=SimpleNamespace(cross_document_claim_count=0, referenced_document_count=1),
        evidence=[_source_anchor()],
        claims=[
            SimpleNamespace(
                text="The paper reports training FLOPs, not kg CO2e.",
                evidence_ids=["E1"],
            )
        ],
        trace=[SimpleNamespace(skill="llm.compare_documents", status="ok")],
    )
    case = {
        "must_refuse": True,
        "required_claim_facts": [],
        "forbidden_quantity_pattern": r"\d+(?:[.,]\d+)?\s*(?:kg|kilograms|公斤)\s*(?:CO2|CO₂)",
    }
    documents = {"bert.pdf": "bert-document"}
    provider = SimpleNamespace(model="deepseek-flash")

    assert _check_case(case, response, documents, provider)["passed"] is True
    response.claims[0].text = "The paper emitted 100 kg CO2e."
    assert (
        "invented_target_quantity" in _check_case(case, response, documents, provider)["failures"]
    )


def test_live_comparison_gate_rejects_related_work_as_own_method() -> None:
    response = SimpleNamespace(
        provider="deepseek",
        model="deepseek-flash",
        insufficient_evidence=False,
        audit=SimpleNamespace(cross_document_claim_count=0, referenced_document_count=1),
        evidence=[_source_anchor()],
        claims=[
            SimpleNamespace(text="The paper uses VLAD dictionary vectors.", evidence_ids=["E1"])
        ],
        trace=[SimpleNamespace(skill="llm.compare_documents", status="ok")],
    )
    case = {
        "min_cross_document_claims": 0,
        "required_claim_facts": [],
        "forbidden_claim_text_contains_any": ["VLAD", "dictionary"],
    }

    result = _check_case(
        case,
        response,
        {"bert.pdf": "bert-document"},
        SimpleNamespace(model="deepseek-flash"),
    )

    assert result["failures"] == ["forbidden_related_work_attribution"]
