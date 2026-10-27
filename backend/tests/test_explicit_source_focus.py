from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import GeneratedClaim
from app.models.document import Document, DocumentStatus
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.answer_completeness import contract_prompt, missing_fields
from app.services.chunking import replace_document_chunks
from app.services.grounded_text import audit_grounded_claims
from app.services.retrieval import HybridRetriever
from app.services.retrieval_policy import (
    evidence_key,
    requested_comparison_pair,
    requested_procedure,
    requested_source_focuses,
    select_source_evidence,
)


def anchor(key, quote, score=0.8, doc="public"):
    return EvidenceAnchor(evidence_id=key, chunk_id=key, document_id=doc, page_number=1,
                          block_ids=[key], bbox=[10, 10, 400, 100], quote=quote,
                          score=score, retrieval_mode="reranked", recall_mode="hybrid")


def test_focus_translates_the_user_concept_never_the_expected_object_or_phase():
    focuses = requested_source_focuses("闭式对应什么对象，免去的采样是什么？")
    assert [q for q, _ in focuses] == ["closed form", "eliminating sampling"]
    assert all("policy" not in q and "fine-tuning" not in q for q, _ in focuses)
    assert requested_source_focuses("采样温度有什么影响？") == [("sampling", r"\bsampling\b")]
    assert requested_source_focuses("闭式结果\n\nCurrent user question:\n作者是谁？") == []
    assert requested_source_focuses("比较两个方法的学习流程。") == []
    assert requested_procedure("比较两个方法的学习流程。")
    assert not requested_procedure("流程\n\nCurrent user question:\n作者是谁？")


def test_focus_budget_preserves_actual_scores_and_never_promotes_low_or_unrecalled_sources():
    best = anchor("top", "Reported benchmark.", 0.95)
    first = anchor("first", "An expression in closed form.", 0.7)
    second = anchor("second", "Eliminating sampling in a stage.", 0.6)
    low = anchor("low", "An expression in closed form.", 0.1)
    groups = [[evidence_key(first)], [evidence_key(second)], [evidence_key(low)]]
    result = select_source_evidence("闭式和采样", [best, first, second, low], 6, 0.18,
                                    source_focus_keys=groups)
    assert result[:3] == [first, second, best]
    assert result[0].score == 0.7 and result[0].quote == first.quote
    assert select_source_evidence("闭式", [best, low], 1, 0.18,
                                  source_focus_keys=[[evidence_key(low)]]) == [best]
    assert select_source_evidence("闭式", [best], 1, 0.18,
                                  source_focus_keys=[[evidence_key(first)]]) == [best]


def test_two_single_paper_summaries_do_not_complete_a_general_cross_paper_question():
    question = "比较两个方法的学习流程。"
    evidence = [anchor("E1", "Method one."), anchor("E2", "Method two.", doc="another")]
    claims = [AnswerClaim(text="第一个方法。", evidence_ids=["E1"]),
              AnswerClaim(text="第二个方法。", evidence_ids=["E2"])]
    assert "BOTH requested" in contract_prompt(question, evidence)
    assert missing_fields(question, claims, evidence) == ["跨论文机制比较的双来源结论"]
    claims.append(AnswerClaim(text="两者的流程不同。", evidence_ids=["E1", "E2"]))
    assert missing_fields(question, claims, evidence) == []
    # A selected single original describing both methods needs no fabricated paper.
    assert missing_fields(question, claims[:1], evidence[:1]) == []


def test_each_explicit_two_original_comparison_requires_its_own_dual_citation():
    question = "Compare Alpha and Beta."
    assert requested_comparison_pair(question) == ["Alpha", "Beta"]
    evidence = [anchor("E1", "Alpha describes a pipeline."),
                anchor("E2", "Beta describes a different pipeline.", doc="another")]
    bad = GeneratedClaim("Alpha uses an objective while Beta uses a different pipeline.", ["E1"])
    good = GeneratedClaim("Alpha and Beta describe different pipelines.", ["E1", "E2"])
    accepted, report = audit_grounded_claims([bad, good], evidence, question=question)
    assert len(accepted) == 1 and accepted[0].text == good.text
    assert "comparison_original_source_omitted" in str(report.rejections[0].reasons)
    # An unasked comparator or one-original question must not fabricate a second source.
    assert audit_grounded_claims([bad], evidence, question="Compare Alpha and Gamma.")[0]
    assert audit_grounded_claims([bad], evidence[:1], question=question)[0]


def test_named_component_alone_is_not_a_claim_about_another_original():
    question = "Compare Alpha and Beta."
    evidence = [anchor("E1", "Alpha introduces adapters."),
                anchor("E2", "Beta uses Alpha adapters.", doc="another")]
    for text in ("Beta uses Alpha adapters.", "Beta将梯度反传入Alpha适配器。",
                 "Beta sends gradients into Low Rank Adapters (Alpha)."):
        accepted, report = audit_grounded_claims(
            [GeneratedClaim(text, ["E2"])], evidence, question=question
        )
        assert accepted[0].text == text and not report.rejections
    for text in ("Beta uses Alpha adapters, whereas Alpha updates the base weights.",
                 "Beta在Alpha组件的基础上改进了机制。",
                 "Beta使用Alpha适配器；Alpha原文有不同设定。"):
        accepted, report = audit_grounded_claims(
            [GeneratedClaim(text, ["E2"])], evidence, question=question
        )
        assert not accepted and "comparison_original_source_omitted" in str(report.rejections)


def test_hybrid_focus_uses_only_selected_real_chunks_and_reranks_before_selection():
    class Vector:
        enabled = True

        def search(self, session, question, document_ids, limit):
            return [anchor("overview", "Benchmark results.", 0.9, document_ids[0])]

    class Reranker:
        enabled, candidate_k = True, 20
        seen = []

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            # Deliberately put definitions behind an unrelated benchmark.
            return sorted([item.model_copy(update={"score": 0.7, "retrieval_mode": "reranked"})
                           for item in evidence], key=lambda item: "Benchmark" not in item.quote)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        docs = []
        for index, texts in enumerate([
            ["The corresponding object has a closed form expression.",
             "Eliminating the need for sampling has a narrow stage scope."],
            ["PRIVATE closed form and eliminating sampling are unrelated."],
        ]):
            doc = Document(original_filename=f"source{index}.pdf", storage_key=f"source{index}.pdf",
                           size_bytes=1, sha256=str(index) * 64, status=DocumentStatus.READY)
            session.add(doc)
            session.flush()
            replace_document_chunks(session, doc.id, {"pages": [
                {"page_number": n + 1, "blocks": [{"block_id": f"p{n+1}-b1", "type": "text",
                  "text": text, "bbox": [10, 10, 400, 100]}]} for n, text in enumerate(texts)]})
            docs.append(doc)
        session.commit()
        reranker = Reranker()
        result = HybridRetriever(session, vector_index=Vector(), reranker=reranker).search(
            "闭式对应什么对象，以及免去的采样是什么？", [docs[0].id], 2)
        assert len(result) == 2 and all(item.document_id == docs[0].id for item in result)
        assert any("closed form" in item.quote for item in result)
        assert any("Eliminating" in item.quote for item in result)
        assert all(item.recall_mode == "hybrid" and item.retrieval_mode == "reranked"
                   and item.score == 0.7 for item in result)
        assert all("PRIVATE" not in item.quote for item in reranker.seen)
    engine.dispose()
