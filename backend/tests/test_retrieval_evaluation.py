from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.evaluation import RetrievalEvaluationSet, evaluate_retrieval
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.document import Document, DocumentStatus
from app.services.chunking import replace_document_chunks
from app.services.retrieval import LexicalRetriever


def _parsed_document() -> dict:
    return {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {
                        "block_id": "p1-b1",
                        "type": "heading",
                        "text": "5 Training",
                        "bbox": [10, 10, 200, 30],
                    },
                    {
                        "block_id": "p1-b2",
                        "type": "text",
                        "text": "Adam uses beta_1 = 0.9 and beta_2 = 0.98.",
                        "bbox": [10, 40, 300, 80],
                    },
                ],
            },
            {
                "page_number": 2,
                "blocks": [
                    {
                        "block_id": "p2-b1",
                        "type": "heading",
                        "text": "6 Results",
                        "bbox": [10, 10, 200, 30],
                    },
                    {
                        "block_id": "p2-b2",
                        "type": "text",
                        "text": "The translation model achieved 28.4 BLEU.",
                        "bbox": [10, 40, 300, 80],
                    },
                ],
            },
        ]
    }


def _dataset() -> RetrievalEvaluationSet:
    return RetrievalEvaluationSet.model_validate(
        {
            "dataset_id": "unit-retrieval-v1",
            "thresholds": {
                "min_case_pass_rate": 1,
                "min_evidence_recall": 1,
                "min_mean_reciprocal_rank": 1,
                "min_anchor_valid_rate": 1,
            },
            "cases": [
                {
                    "case_id": "optimizer-beta",
                    "question": "What beta_1 value did Adam use?",
                    "documents": [{"arxiv_id": "1706.03762", "arxiv_version": 1}],
                    "expectations": [
                        {
                            "text_contains_any": ["beta_1 = 0.9"],
                            "page_numbers": [1],
                            "source_types": ["text"],
                        }
                    ],
                },
                {
                    "case_id": "translation-bleu",
                    "question": "What BLEU score did the translation model achieve?",
                    "documents": [{"title_contains": "Attention"}],
                    "expectations": [
                        {"text_contains_any": ["28.4 BLEU"], "page_numbers": [2]}
                    ],
                },
            ],
        }
    )


def test_evaluation_reports_grounded_retrieval_metrics() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = Document(
            original_filename="1706.03762v1.pdf",
            storage_key="attention.pdf",
            size_bytes=100,
            sha256="d" * 64,
            status=DocumentStatus.READY,
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            arxiv_version=1,
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _parsed_document())
        session.commit()

        report = evaluate_retrieval(
            session,
            _dataset(),
            retriever=LexicalRetriever(session),
        )

    assert report["status"] == "passed"
    assert report["metrics"]["case_pass_rate"] == 1
    assert report["metrics"]["evidence_recall"] == 1
    assert report["metrics"]["mean_reciprocal_rank"] == 1
    assert report["metrics"]["anchor_valid_rate"] == 1
    assert all(case["evidence"][0]["bbox"] for case in report["cases"])


def test_missing_document_is_an_explicit_failure() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        report = evaluate_retrieval(
            session,
            _dataset(),
            retriever=LexicalRetriever(session),
        )

    assert report["status"] == "failed"
    assert report["metrics"]["case_pass_rate"] == 0
    assert {case["status"] for case in report["cases"]} == {"missing_document"}


@pytest.mark.parametrize("filename", ["local_chinese_thesis.json", "attention_v1.json"])
def test_committed_evaluation_sets_validate(filename: str) -> None:
    path = Path(__file__).resolve().parents[1] / "evals" / filename
    dataset = RetrievalEvaluationSet.model_validate_json(path.read_text(encoding="utf-8"))

    assert dataset.cases
    assert len({case.case_id for case in dataset.cases}) == len(dataset.cases)
