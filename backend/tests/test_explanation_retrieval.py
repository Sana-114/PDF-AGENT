import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.retrieval import HybridRetriever, _expand_query
from app.services.retrieval_policy import (
    evidence_key,
    explanation_statement,
    select_source_evidence,
)


def anchor(key, quote, *, score=0.7, doc="doc", source="text"):
    return EvidenceAnchor(evidence_id=key, chunk_id=key, document_id=doc, page_number=6,
                          block_ids=[key], bbox=[10, 10, 400, 100], quote=quote, score=score,
                          source_type=source, retrieval_mode="reranked", recall_mode="hybrid")


@pytest.mark.parametrize("source", ["reference", "abstract", "table"])
def test_causal_terms_in_other_source_types_are_not_body_explanations(source):
    assert not explanation_statement(anchor("x", "The motivation for this method", source=source))


def test_promotion_needs_why_intent_and_original_scored_body_quote():
    best = anchor("best", "Our method uses repeated updates.", score=0.9)
    why = anchor("why", "The motivation is to avoid unstable updates.")
    items = [best, why]
    keys = [evidence_key(why)]
    selected = select_source_evidence("为什么该方法有效？", items, 1, 0.18, explanation_keys=keys)
    assert selected == [why] and selected[0].score == 0.7
    assert selected[0].quote == why.quote and selected[0].bbox == why.bbox
    assert selected[0].retrieval_mode == "reranked" and selected[0].recall_mode == "hybrid"
    assert select_source_evidence("训练设置是什么？", items, 1, 0.18,
                                  explanation_keys=keys) == [best]
    assert select_source_evidence("Why?", items, 0, 0.18, explanation_keys=keys) == []


def test_low_score_unrecalled_and_noncausal_candidates_do_not_get_promoted():
    best = anchor("best", "Our method works.")
    low = anchor("low", "Because the objective controls drift.", score=0.1)
    unselected = anchor("unselected", "The rationale is to avoid drift.")
    ordinary = anchor("ordinary", "Repeated updates are used.")
    assert select_source_evidence("Why does it work?", [best, low, unselected, ordinary], 1,
                                  0.18, explanation_keys=[evidence_key(low),
                                                          evidence_key(ordinary)]) == [best]


def test_explanation_coverage_is_one_per_paper_and_at_most_a_third():
    best = anchor("best", "Overview.")
    first = anchor("first", "Because the update is bounded.")
    repeat = anchor("repeat", "Therefore the method works.")
    second = anchor("second", "The motivation is bounded updates.", doc="other")
    third = anchor("third", "The rationale is limited drift.", doc="third")
    items = [best, first, repeat, second, third]
    selected = select_source_evidence("Why?", items, 6, 0.18,
                                      explanation_keys=[evidence_key(i) for i in items])
    assert selected[:3] == [first, second, best]
    assert len({evidence_key(item) for item in selected}) == len(selected)


def test_reference_question_preserves_ordinary_rank_even_with_why():
    citation = anchor("ref", "A reference", source="reference")
    body = anchor("body", "Because it avoids drift.")
    assert select_source_evidence("为什么引用该参考文献？", [citation, body], 1, 0.18,
                                  explanation_keys=[evidence_key(body)]) == [citation]


def test_hybrid_explanation_recall_is_scoped_and_reranked_without_answer_injection():
    class Vector:
        enabled = True

        def search(self, session, question, document_ids, limit):
            return [anchor("vector", "Method overview", doc=document_ids[0], score=0.9)]

    class Reranker:
        enabled, candidate_k = True, 20
        seen = []
        scoring_query = None

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            self.scoring_query = question
            ordered = sorted(evidence, key=explanation_statement)
            return [item.model_copy(update={"retrieval_mode": "reranked", "score": 0.6})
                    for item in ordered[:top_k]]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        docs = []
        for n, text in enumerate([
            "The motivation for this objective is to prevent unstable multiple minibatch updates.",
            "The rationale for this objective is a PRIVATE unrelated mechanism.",
        ]):
            doc = Document(original_filename=f"example{n}.pdf", storage_key=f"example{n}.pdf",
                           size_bytes=1, sha256=str(n) * 64, status=DocumentStatus.READY)
            session.add(doc)
            session.flush()
            replace_document_chunks(session, doc.id, {"pages": [{"page_number": 6, "blocks": [{
                "block_id": "p6-b1", "type": "text", "text": text,
                "bbox": [10, 10, 400, 100],
            }]}]})
            docs.append(doc)
        session.commit()
        reranker = Reranker()
        results = HybridRetriever(session, vector_index=Vector(), reranker=reranker).search(
            "为什么多轮小批量更新有效？", [docs[0].id], 1)
        assert len(results) == 1 and "motivation" in results[0].quote
        assert results[0].document_id == docs[0].id and "PRIVATE" not in results[0].quote
        assert results[0].block_ids == ["p6-b1"] and results[0].page_number == 6
        assert results[0].score == 0.6 and results[0].recall_mode == "hybrid"
        assert results[0].retrieval_mode == "reranked" and results[0].evidence_id == "E1"
        assert any(explanation_statement(item) for item in reranker.seen)
        assert "为什么多轮小批量更新有效？" in reranker.scoring_query
        assert reranker.scoring_query.startswith("Explain the mechanism and motivation:")
        assert all(item.document_id == docs[0].id for item in reranker.seen)
        expanded = _expand_query("多轮小批量更新")
        assert all(word in expanded for word in ["multiple", "epochs", "minibatch", "update"])
