import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.retrieval import HybridRetriever, LexicalRetriever
from app.services.retrieval_policy import (
    evidence_key,
    procedure_passage,
    select_source_evidence,
)


@pytest.mark.parametrize("section,text,source,expected", [
    ("3.1 Methodology", "Step 1: Collect examples and train a model.", "text", True),
    (None, "Stage 2: Optimize the objective.", "text", True),
    ("5 Future work", "Step 1: A proposed new pipeline.", "text", False),
    ("Discussion", "Our future pipeline could have several stages.", "text", False),
    ("Related work", "Step 2: A cited procedure.", "text", False),
    ("3 Methods", "3 Methods", "text", False),
    ("Algorithm", "Step 1: A recipe.", "table", False),
    (None, "There is a pipeline in our benchmark.", "text", False),
])
def test_procedural_role_is_not_incidental_pipeline_or_empty_heading(
    section, text, source, expected,
):
    assert procedure_passage(section, text, source) is expected


def item(key, text="Step 1: Train a model.", score=0.7):
    return EvidenceAnchor(evidence_id=key, chunk_id=key, document_id="selected",
                          page_number=4, block_ids=[key], bbox=[10, 10, 400, 200],
                          section="Methodology", source_type="text", quote=text, score=score,
                          retrieval_mode="reranked", recall_mode="hybrid")


def test_procedure_budget_needs_current_intent_recall_and_score_and_keeps_anchors():
    ordinary = item("best", "Results of our evaluation.", 0.95)
    steps = [item(f"s{n}", f"Step {n}: Perform an operation.") for n in range(1, 6)]
    low = item("low", score=0.01)
    unknown = item("unrecalled")
    keys = [evidence_key(v) for v in [low, *steps]]
    ranked = [ordinary, low, unknown, *steps]
    selected = select_source_evidence("比较学习流程", ranked, 6, 0.18, procedure_keys=keys)
    assert selected[:3] == [steps[0], steps[1], ordinary]
    assert selected[0].model_dump() == steps[0].model_dump()
    assert select_source_evidence("作者是谁？", ranked, 1, 0.18, procedure_keys=keys) == [ordinary]
    assert select_source_evidence("流程参考文献", ranked, 1, 0.18,
                                  procedure_keys=keys) == [ordinary]
    # Only one shared third-of-budget for procedural and other focus hints.
    selected = select_source_evidence("学习流程及闭式", ranked, 6, 0.18, procedure_keys=keys,
                                      source_focus_keys=[[evidence_key(unknown)]])
    assert selected[:3] == [steps[0], steps[1], ordinary]


def test_procedure_filter_runs_before_bm25_top_k_and_same_reranker_with_scope():
    class Vector:
        enabled = True

        def search(self, session, question, document_ids, limit):
            return []

    class Reranker:
        enabled, candidate_k = True, 20

        def rerank(self, question, evidence, top_k):
            self.seen = evidence
            assert question.startswith("Explain the procedure, methodology and training steps:")
            # Correct method steps must survive even when a results passage ranks first.
            return sorted([v.model_copy(update={"score": 0.6, "retrieval_mode": "reranked"})
                           for v in evidence], key=lambda v: procedure_passage(
                               v.section, v.quote, v.source_type))

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        docs = []
        for n in range(2):
            doc = Document(original_filename=f"source{n}.pdf", storage_key=f"source{n}.pdf",
                           size_bytes=1, sha256=str(n) * 64, status=DocumentStatus.READY)
            session.add(doc)
            session.flush()
            pages = [{"page_number": j + 1, "blocks": [{"type": "text",
                      "block_id": f"p{j+1}-b1", "bbox": [10, 10, 400, 200],
                      "text": "Our future pipeline procedure steps stages."}]} for j in range(15)]
            pages.append({"page_number": 16, "blocks": [{"type": "text", "block_id": "p16-b1",
                          "bbox": [10, 10, 400, 200],
                          "text": "Step 1: Train a model using our method procedure."
                          if n == 0 else "Step 1: PRIVATE method procedure."}]})
            replace_document_chunks(session, doc.id, {"pages": pages})
            docs.append(doc)
        session.commit()
        recalled = LexicalRetriever(session).search("method procedure steps", [docs[0].id], 1,
                                                    procedure_only=True)
        assert len(recalled) == 1 and recalled[0].page_number == 16
        reranker = Reranker()
        result = HybridRetriever(session, Vector(), reranker).search("学习流程", [docs[0].id], 1)
        assert result[0].page_number == 16 and result[0].score == 0.6
        assert result[0].retrieval_mode == "reranked" and result[0].block_ids == ["p16-b1"]
        assert all(v.document_id == docs[0].id and "PRIVATE" not in v.quote for v in reranker.seen)
    engine.dispose()
