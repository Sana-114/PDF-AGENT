from types import SimpleNamespace

import pytest

from app.schemas.comparison import CrossPaperComparisonRead
from scripts.evaluate_real_pdf_comparison import _check_case


@pytest.mark.parametrize(
    ("recall", "search_status", "expected"),
    [("hybrid", "ok", True), ("lexical", "ok", False),
     (None, "ok", False), ("hybrid", "degraded", False)],
)
def test_standard_gate_requires_proven_hybrid_recall_even_with_reranking(
    recall, search_status, expected,
):
    response = CrossPaperComparisonRead.model_validate({
        "question": "Unsupported quantity?", "answer": "Not supported.",
        "papers": [], "claims": [], "stats": {}, "audit": {},
        "evidence": [{
            "evidence_id": "E1", "document_id": "A", "page_number": 1,
            "block_ids": ["p1-b1"], "bbox": [0, 0, 100, 100],
            "quote": "Original paper text.", "score": 0.9,
            "retrieval_mode": "reranked", "recall_mode": recall,
        }],
        "insufficient_evidence": True, "provider": "deepseek", "model": "deepseek-flash",
        "trace": [
            {"skill": "search_evidence", "status": search_status,
             "summary": "retrieval", "duration_ms": 1},
            {"skill": "llm.compare_documents", "status": "ok",
             "summary": "actual model", "duration_ms": 1},
        ],
    })
    result = _check_case(
        {"must_refuse": True, "required_claim_facts": []}, response, {"paper": "A"},
        SimpleNamespace(model="deepseek-flash"), require_hybrid_recall=True,
    )
    assert result["passed"] is expected
    assert ("retrieval_bypassed_hybrid_recall" in result["failures"]) is not expected
