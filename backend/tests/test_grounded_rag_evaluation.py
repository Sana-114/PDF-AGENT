from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.evaluation import GroundedRAGEvaluationSet, evaluate_grounded_rag
from app.evaluation.grounded_rag import _claim_is_grounded
from app.llm.base import GeneratedAnswer, GeneratedClaim, LLMResponseError
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.retrieval import LexicalRetriever


class GroundedProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    async def generate_grounded_answer(self, question, evidence):
        del question
        anchor = evidence[0]
        return GeneratedAnswer(
            answer="Adam used beta_1 = 0.9 and beta_2 = 0.98.",
            claims=[
                GeneratedClaim(
                    text="Adam used beta_1 = 0.9 and beta_2 = 0.98.",
                    evidence_ids=[anchor.evidence_id],
                )
            ],
        )


class FailingProvider(GroundedProvider):
    async def generate_grounded_answer(self, question, evidence):
        del question, evidence
        raise LLMResponseError("simulated provider outage")


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
                        "text": "Adam used beta_1 = 0.9 and beta_2 = 0.98.",
                        "bbox": [10, 40, 300, 80],
                    },
                ],
            }
        ]
    }


def _dataset() -> GroundedRAGEvaluationSet:
    return GroundedRAGEvaluationSet.model_validate(
        {
            "dataset_id": "unit-grounded-rag-v1",
            "expected_provider": "deepseek",
            "thresholds": {
                "min_case_pass_rate": 1,
                "min_answer_fact_recall": 1,
                "min_evidence_recall": 1,
                "min_anchor_valid_rate": 1,
                "min_citation_valid_rate": 1,
                "min_grounded_claim_rate": 1,
                "min_refusal_pass_rate": 1,
                "min_provider_match_rate": 1,
                "max_fallback_rate": 0,
            },
            "cases": [
                {
                    "case_id": "optimizer-beta",
                    "question": "What beta values did Adam use?",
                    "documents": [{"arxiv_id": "1706.03762", "arxiv_version": 1}],
                    "evidence_expectations": [
                        {
                            "text_contains_any": ["beta_1 = 0.9", "beta_2 = 0.98"],
                            "page_numbers": [1],
                        }
                    ],
                    "answer_expectations": [
                        {"text_contains_any": ["0.9"]},
                        {"text_contains_any": ["0.98"]},
                    ],
                },
                {
                    "case_id": "unsupported-emissions",
                    "question": "Exact kilograms of CO2-equivalent emissions?",
                    "documents": [{"title_contains": "Attention"}],
                    "must_refuse": True,
                },
            ],
        }
    )


def _add_document(session: Session) -> None:
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


@pytest.mark.asyncio
async def test_grounded_rag_evaluator_covers_answer_citations_and_refusal() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _add_document(session)
        report = await evaluate_grounded_rag(
            session,
            _dataset(),
            provider=GroundedProvider(),  # type: ignore[arg-type]
            retriever=LexicalRetriever(session),
        )

    assert report["status"] == "passed"
    assert report["metrics"]["case_pass_rate"] == 1
    assert report["metrics"]["answer_fact_recall"] == 1
    assert report["metrics"]["evidence_recall"] == 1
    assert report["metrics"]["anchor_valid_rate"] == 1
    assert report["metrics"]["citation_valid_rate"] == 1
    assert report["metrics"]["grounded_claim_rate"] == 1
    assert report["metrics"]["refusal_pass_rate"] == 1
    assert report["metrics"]["provider_match_rate"] == 1
    assert report["metrics"]["fallback_rate"] == 0
    assert report["metrics"]["provider_calls"] == 1


@pytest.mark.asyncio
async def test_grounded_rag_evaluator_exposes_provider_fallback() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _add_document(session)
        dataset = _dataset().model_copy(
            update={"cases": [_dataset().cases[0]]},
            deep=True,
        )
        report = await evaluate_grounded_rag(
            session,
            dataset,
            provider=FailingProvider(),  # type: ignore[arg-type]
            retriever=LexicalRetriever(session),
        )

    assert report["status"] == "failed"
    assert report["metrics"]["fallback_rate"] == 1
    assert report["cases"][0]["generation_status"] == "fallback"
    assert any("fallback_rate" in failure for failure in report["threshold_failures"])


@pytest.mark.asyncio
async def test_grounded_rag_evaluator_rejects_retrieval_mode_fallback() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _add_document(session)
        report = await evaluate_grounded_rag(
            session,
            _dataset(),
            provider=GroundedProvider(),  # type: ignore[arg-type]
            retriever=LexicalRetriever(session),
            required_retrieval_mode="reranked",
        )

    assert report["status"] == "failed"
    assert report["metrics"]["retrieval_mode_match_rate"] == 0
    assert all(not case["retrieval_mode_valid"] for case in report["cases"])
    assert any("required=reranked" in item for item in report["threshold_failures"])


def test_committed_grounded_rag_dataset_validates() -> None:
    path = Path(__file__).resolve().parents[1] / "evals" / "attention_v1_grounded.json"
    dataset = GroundedRAGEvaluationSet.model_validate_json(path.read_text(encoding="utf-8"))

    assert dataset.expected_provider == "deepseek"
    assert dataset.cases[-1].must_refuse is True
    assert len({case.case_id for case in dataset.cases}) == len(dataset.cases)


def test_claim_grounding_accepts_supported_extra_fact_and_rejects_new_number() -> None:
    evidence = EvidenceAnchor(
        evidence_id="E4",
        document_id="doc-1",
        page_number=7,
        block_ids=["p7-b10"],
        quote="Training took 3 . 5 days on 8 P100 GPUs.",
        score=1,
    )

    assert _claim_is_grounded(
        "big 模型的训练在 8 块 P100 GPU 上耗时 3.5 天。",
        ["E4"],
        {"E4": evidence},
        [],
    )
    assert not _claim_is_grounded(
        "big 模型的训练在 8 块 P100 GPU 上耗时 9.5 天。",
        ["E4"],
        {"E4": evidence},
        [],
    )
