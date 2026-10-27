from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.evidence_fields import context_statement, select_field_evidence
from app.services.retrieval import HybridRetriever, _expand_query


def anchor(quote, key, score=0.8, source_type="text", document_id="doc"):
    return EvidenceAnchor(evidence_id="candidate", chunk_id=key, document_id=document_id,
                          page_number=8, block_ids=[key], quote=quote, score=score,
                          source_type=source_type, recall_mode="hybrid", retrieval_mode="reranked")


def test_context_field_does_not_promote_architecture_dimension_or_sampling_caption():
    table = anchor("GPT-3 XL 1.3B 24 2048 24; d_model n_heads", "table", source_type="table")
    caption = anchor("Contexts are 768 tokens, completions 256 tokens.", "caption")
    examples = anchor("as many demonstrations as fit into the context window (typically 10 to 100)",
                      "examples")
    statement = anchor("All models use a context window of n_ctx = 4096 tokens.", "window")
    assert not context_statement(table) and not context_statement(caption)
    assert not context_statement(examples)
    assert context_statement(statement)
    selected = select_field_evidence("最大上下文是多少？", [table, caption, statement], 2, 0.25)
    assert [item.chunk_id for item in selected] == ["window", "table"]
    assert selected[0].score == 0.8 and selected[0].recall_mode == "hybrid"


def test_context_promotion_is_bounded_per_document_and_keeps_low_scores_out():
    items = [anchor("context window = 4096 tokens", "low", 0.1),
             anchor("context window = 8192 tokens", "first"),
             anchor("context window = 8192 tokens", "second"),
             anchor("context size from 512 to 1024 tokens", "other", document_id="other")]
    assert [item.chunk_id for item in select_field_evidence("上下文", items, 2, 0.25)] == [
        "first", "other"
    ]
    assert select_field_evidence("作者是谁？", items, 2, 0.25) == items[:2]
    assert "nctx" in _expand_query("最大上下文是多少？")


def test_hybrid_context_recall_respects_document_scope_and_reranks_promoted_candidates():
    class Vector:
        enabled = True

        def search(self, session, question, document_ids, limit):
            return [anchor("Model architecture d_model 2048 parameters", "vector", 0.9,
                           document_id=document_ids[0]).model_copy(
                               update={"retrieval_mode": "vector"})]

    class Reranker:
        enabled, candidate_k = True, 20
        seen = []

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            # Simulate a definition scoring below a broad architecture chunk.
            ordered = sorted(evidence, key=lambda item: context_statement(item))
            return [item.model_copy(update={"retrieval_mode": "reranked", "score": 0.6})
                    for item in ordered[:top_k]]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        documents = []
        for index, size in enumerate([4096, 9999]):
            doc = Document(original_filename=f"scope{index}.pdf", storage_key=f"scope{index}.pdf",
                           size_bytes=100, sha256=str(index) * 64, status=DocumentStatus.READY)
            session.add(doc)
            session.flush()
            replace_document_chunks(session, doc.id, {"pages": [{"page_number": 8, "blocks": [{
                "block_id": "p8-b1", "type": "text", "bbox": [10, 200, 500, 230],
                "text": f"All models use a context window of n_ctx = {size} tokens.",
            }]}]})
            documents.append(doc)
        session.commit()
        reranker = Reranker()
        results = HybridRetriever(session, vector_index=Vector(), reranker=reranker).search(
            "从模型一到模型三，最大上下文是多少？", [documents[0].id], 1
        )
        assert results and "4096" in results[0].quote and "9999" not in results[0].quote
        assert results[0].document_id == documents[0].id and results[0].bbox
        assert results[0].evidence_id == "E1" and results[0].retrieval_mode == "reranked"
        assert results[0].recall_mode == "hybrid"
        assert any(context_statement(item) for item in reranker.seen)
