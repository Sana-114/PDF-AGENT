from types import SimpleNamespace

from app.schemas.agent import EvidenceAnchor
from scripts.evaluate_real_pdf_comparison import _check_case, _check_fact


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
