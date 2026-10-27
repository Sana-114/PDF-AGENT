import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.evidence_fields import (
    parameter_statement,
    parameter_values,
    select_field_evidence,
)
from app.services.retrieval import HybridRetriever


def anchor(quote, key, *, doc="local", score=0.8, page=4, source="text"):
    return EvidenceAnchor(evidence_id=key, chunk_id=key, document_id=doc, page_number=page,
                          block_ids=[key], bbox=[10, 10, 100, 50], quote=quote,
                          source_type=source, score=score, recall_mode="hybrid",
                          retrieval_mode="reranked")


@pytest.mark.parametrize("quote", [
    "d_model 2048, batch size 3M tokens, training uses 200B tokens.",
    "All parameters are trained using 200B tokens.",
    "Table 3. Accuracy for a 70M model on the evaluation benchmark.",
    "BERT BLEU 42.3; training cost 8B FLOPs.",
])
def test_other_quantities_and_performance_tables_are_not_count_definitions(quote):
    assert not parameter_statement(anchor(quote, "unrelated"))


def test_parameter_table_values_and_prose_keep_source_units():
    table = anchor("Parameters Layers d_model\n70M 8 512; 450M 24 1024", "count")
    assert parameter_values(table) == {"quantity:70000000", "quantity:450000000"}
    for quote in ["We train a 450M parameter Transformer.", "Number of parameters = 450M.",
                  "该模型的参数量为4.5亿。"]:
        assert parameter_values(anchor(quote, "prose")) == {"quantity:450000000"}
    for source in ["reference", "figure"]:
        assert not parameter_statement(table.model_copy(update={"source_type": source}))
    assert parameter_values(anchor("参数量从80M到900M", "range")) == {
        "quantity:80000000", "quantity:900000000"}


def test_exact_table_and_same_page_baseline_survive_a_small_budget_without_score_rewriting():
    performance = anchor("Accuracy of this model is 42.3", "performance", score=0.99)
    abstract = anchor("Our largest model is a 0.4B parameter Transformer", "abstract", score=0.9)
    table = anchor("Parameters Layers d_model\n70M 8 512; 450M 24 1024", "table", score=0.5)
    baseline = anchor("The smallest model is equivalent to the original model.",
                      "baseline", score=0.8)
    result = select_field_evidence("比较模型参数量",
                                   [performance, abstract, baseline, table], 3, 0.18)
    assert [item.chunk_id for item in result] == ["table", "baseline", "performance"]
    assert result[0] is table and result[0].score == 0.5 and result[0].bbox == table.bbox
    assert result[0].quote == table.quote and result[0].recall_mode == "hybrid"
    assert select_field_evidence("参数量", [abstract, table], 1, 0.6) == [abstract]
    for budget in [0, -1]:
        assert select_field_evidence("参数量和上下文", [table], budget, 0.18) == []


def test_equivalence_pair_must_be_same_document_same_page_and_already_strong():
    table = anchor("Parameters Layers d_model\n70M 8 512", "table")
    good = anchor("The smallest model is equivalent to the original model", "good")
    foreign = good.model_copy(update={"chunk_id": "foreign", "document_id": "other"})
    other_page = good.model_copy(update={"chunk_id": "other-page", "page_number": 9})
    weak = good.model_copy(update={"chunk_id": "weak", "score": 0.05})
    reference = good.model_copy(update={"chunk_id": "ref", "source_type": "reference"})
    result = select_field_evidence("参数量",
                                   [foreign, other_page, weak, reference, good, table], 2, 0.2)
    assert [item.chunk_id for item in result] == ["table", "good"]


def test_compound_fields_share_budget_and_duplicate_chunk_is_not_promoted_twice():
    count = anchor("Parameters Layers d_model\n70M 8 512", "count")
    window = anchor("context window = 1536 tokens", "window")
    assert select_field_evidence("参数量和最大上下文", [count, window], 2, 0.2) == [window, count]
    both = anchor(count.quote + "; context window = 1536 tokens", "both")
    assert select_field_evidence("参数量和上下文", [both], 2, 0.2) == [both]


def test_hybrid_field_recall_rejects_foreign_vector_hits_and_reranks_real_scoped_table():
    class Vector:
        enabled = True

        def search(self, *args):
            return [anchor("Parameters Layers d_model\n999M 99 9999", "private",
                           doc="outside-selected-library")]

    class Reranker:
        enabled, candidate_k, seen = True, 20, []

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            # Score the correct count table below many incidental matches.
            ordered = sorted(evidence, key=parameter_statement)
            return [item.model_copy(update={"retrieval_mode": "reranked", "score": 0.5})
                    for item in ordered[:top_k]]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        doc = Document(original_filename="local.pdf", storage_key="local.pdf", size_bytes=1,
                       sha256="0" * 64, status=DocumentStatus.READY)
        session.add(doc)
        session.flush()
        replace_document_chunks(session, doc.id, {"pages": [{"page_number": 4, "blocks": [
            {"block_id": "p4-b1", "type": "text", "bbox": [10, 10, 100, 20],
             "text": "Parameters Layers d_model\n70M 8 512"},
            *[{"block_id": f"p4-b{i+2}", "type": "text", "bbox": [10, 30, 100, 40],
               "text": f"Parameter evaluation performance comparison {i}"} for i in range(20)],
        ]}]})
        session.commit()
        reranker = Reranker()
        result = HybridRetriever(session, vector_index=Vector(), reranker=reranker).search(
            "对比模型参数量", [doc.id], 1)
        assert result and "70M" in result[0].quote
        assert all(item.document_id == doc.id and "999M" not in item.quote
                   for item in [*result, *reranker.seen])
        # Rejecting all foreign vector hits correctly exposes lexical fallback.
        assert result[0].recall_mode == "lexical" and result[0].retrieval_mode == "reranked"
    engine.dispose()
