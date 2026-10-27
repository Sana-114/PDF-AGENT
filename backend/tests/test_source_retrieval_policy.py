import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.retrieval import HybridRetriever, LexicalRetriever, _expand_query
from app.services.retrieval_policy import evidence_key, select_source_evidence


def anchor(key, *, source="text", doc="doc", score=0.7):
    return EvidenceAnchor(evidence_id=key, chunk_id=key, document_id=doc, page_number=4,
                          block_ids=[key], bbox=[10, 10, 300, 60], quote=f"Original {key}",
                          source_type=source, score=score, retrieval_mode="reranked",
                          recall_mode="hybrid")


def test_body_priority_is_not_applied_to_reference_questions():
    citation = anchor("ref", source="reference", score=0.98)
    text = anchor("body", score=0.7)
    assert select_source_evidence("如何定义这个方法？", [citation, text], 1, 0.18) == [text]
    for question in ["给出参考文献完整引用", "What is this reference?", "citation DOI"]:
        assert select_source_evidence(question, [citation, text], 1, 0.18) == [citation]
    assert select_source_evidence("比较两篇文献的训练方法", [citation, text], 1, 0.18) == [text]


def test_summary_coverage_is_bounded_scored_and_keeps_quote_and_provenance():
    items = [anchor("best"), anchor("summary1", source="abstract", score=0.6),
             anchor("summary2", source="abstract", score=0.5),
             anchor("summary3", source="abstract", score=0.4),
             anchor("low", source="abstract", score=0.1)]
    focused = [evidence_key(item) for item in items[1:]]
    result = select_source_evidence("训练数据有多少小时？", items, 6, 0.18, summary_keys=focused)
    assert result[:3] == [items[1], items[2], items[0]]
    assert result[0].score == 0.6 and result[0].quote == "Original summary1"
    assert result[0].recall_mode == "hybrid" and result[0].retrieval_mode == "reranked"
    assert select_source_evidence("附录里的设置是多少？", items, 1, 0.18,
                                  summary_keys=focused) == [items[0]]
    assert select_source_evidence("训练数据有多少小时？", items, 0, 0.18) == []


def test_author_coverage_has_one_anchor_per_document_and_ignores_low_scores():
    items = [anchor("noise"), anchor("low", score=0.1), anchor("credit"),
             anchor("credit2"), anchor("other", doc="other")]
    result = select_source_evidence("署名是谁？", items, 3, 0.18,
                                     author_keys=[evidence_key(i) for i in items[1:]])
    assert result == [items[2], items[4], items[0]]


def test_bilingual_glossary_expands_terms_not_answers():
    question = "如何定义涌现能力；监督数据小时数；律师资格考试分位；推理和并行？"
    expanded = _expand_query(question)
    for term in ["emergent", "definition", "hours", "bar", "percentile", "parallel", "reasoning"]:
        assert term in expanded
    assert not any(value in expanded for value in ["680,000", "7x", "10%", "BigScience"])


def block(key, text, kind="text"):
    return {"block_id": key, "text": text, "type": kind, "bbox": [20, 40, 450, 70]}


def test_hybrid_recalls_real_team_credit_without_inventing_a_complete_author_list():
    class Vector:
        enabled = True

        def search(self, session, question, document_ids, limit):
            return [anchor("bibliography", source="reference", doc=document_ids[0], score=0.95)
                    .model_copy(update={"retrieval_mode": "vector"})]

    class Reranker:
        enabled, candidate_k = True, 20
        seen = []

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            # Put the relevant credit below bibliography to test final coverage.
            ordered = sorted(evidence, key=lambda e: e.source_type != "reference")
            return [e.model_copy(update={"retrieval_mode": "reranked", "score": 0.6})
                    for e in ordered[:top_k]]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        documents = []
        for n, team in enumerate(["Research Collective", "Private Other Team"]):
            doc = Document(original_filename=f"paper{n}.pdf", storage_key=f"paper{n}.pdf",
                           size_bytes=1, sha256=str(n) * 64, status=DocumentStatus.READY,
                           authors_json=json.dumps([team, "Missing Name"]))
            session.add(doc)
            session.flush()
            replace_document_chunks(session, doc.id, {"authors": [team, "Missing Name"],
                "pages": [{"page_number": 1, "blocks": [block("p1-b1", f"{team}*")]},
                          {"page_number": 9, "blocks": [
                              block("p9-b1", "6 Contributions", "heading"),
                              block("p9-b2", "Authors are assigned categories.")]}]})
            documents.append(doc)
        session.commit()
        reranker = Reranker()
        result = HybridRetriever(session, vector_index=Vector(), reranker=reranker).search(
            "论文署名团队是谁？", [documents[0].id], 1,
        )
        assert result[0].quote == "Research Collective*"
        assert result[0].page_number == 1 and result[0].block_ids == ["p1-b1"]
        assert result[0].retrieval_mode == "reranked" and result[0].recall_mode == "hybrid"
        assert all(item.document_id == documents[0].id for item in reranker.seen)
        assert "Missing Name" not in result[0].quote
        documents[0].authors_json = '["Nonexistent Person"]'
        session.commit()
        assert LexicalRetriever(session).front_matter([documents[0].id]) == []


def test_summary_recall_reaches_reranker_without_replacing_body_or_citation_paths():
    class Vector:
        enabled = True

        def search(self, session, question, document_ids, limit):
            return [anchor("unrelated", doc=document_ids[0], score=0.9)
                    .model_copy(update={"retrieval_mode": "vector"})]

    class Reranker:
        enabled, candidate_k = True, 20
        seen = []

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            ordered = sorted(evidence, key=lambda e: e.source_type == "abstract")
            return [e.model_copy(update={"retrieval_mode": "reranked", "score": 0.5})
                    for e in ordered[:top_k]]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        doc = Document(original_filename="speech.pdf", storage_key="speech.pdf", size_bytes=1,
                       sha256="f" * 64, status=DocumentStatus.READY)
        session.add(doc)
        session.flush()
        replace_document_chunks(session, doc.id, {"pages": [{"page_number": 4, "blocks": [
            block("p4-b1", "Abstract", "heading"),
            block("p4-b2", "Our multilingual training uses 314,000 hours of supervision."),
            block("p4-b3", "1 Introduction", "heading"),
            block("p4-b4", "We discuss training experiments.")]}]})
        session.commit()
        reranker = Reranker()
        result = HybridRetriever(session, vector_index=Vector(), reranker=reranker).search(
            "多语言监督训练数据有多少小时？", [doc.id], 1,
        )
        assert result[0].source_type == "abstract" and "314,000" in result[0].quote
        assert result[0].page_number == 4 and result[0].score == 0.5
        assert result[0].recall_mode == "hybrid" and result[0].retrieval_mode == "reranked"
        assert any(e.source_type == "abstract" for e in reranker.seen)


def test_front_matter_filter_runs_before_top_k_not_after_many_body_matches():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        doc = Document(original_filename="collective.pdf", storage_key="collective.pdf",
                       size_bytes=1, sha256="e" * 64, status=DocumentStatus.READY,
                       authors_json='["Example Workshop", "Missing Name"]')
        session.add(doc)
        session.flush()
        text = (
            "Example Workshop*\n" + "Unrelated contributor names and institutions " * 40
        ).strip()
        pages = [{"page_number": 1, "blocks": [block("p1-b1", text)]}]
        for page in range(4, 35):
            pages.append({"page_number": page, "blocks": [
                block(f"p{page}-b1", "Contributions", "heading"),
                block(f"p{page}-b2", "Example Workshop authors affiliations")],
            })
        replace_document_chunks(session, doc.id, {"pages": pages})
        session.commit()
        result = LexicalRetriever(session).front_matter([doc.id])
        assert len(result) == 1 and result[0].page_number == 1
        assert result[0].quote == text and result[0].block_ids == ["p1-b1"]
