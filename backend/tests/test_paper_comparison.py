from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import agent as agent_routes
from app.core.database import Base, get_db
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.main import app
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.paper_comparison import PaperComparisonService


class RecordingComparisonProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def generate_grounded_answer(self, question, evidence):
        self.questions.append(question)
        assert [item.evidence_id for item in evidence] == ["E1", "E2", "E3", "E4"]
        return GeneratedAnswer(
            answer="两篇论文在注意力机制上有继承关系，但训练设置不同。",
            claims=[
                GeneratedClaim(
                    text="[AGREEMENT] 两篇论文都使用注意力机制。",
                    evidence_ids=["E1", "E3"],
                ),
                GeneratedClaim(
                    text="[DIFFERENCE] 两篇论文采用不同训练设置。",
                    evidence_ids=["E2", "E4"],
                ),
                GeneratedClaim(
                    text="[CONFLICT] 只有一篇论文支持的伪冲突。",
                    evidence_ids=["E1"],
                ),
                GeneratedClaim(
                    text="未标记的跨来源结论。",
                    evidence_ids=["E1", "E3"],
                ),
            ],
        )


class BalancedRetriever:
    def __init__(self, unsupported_document_id: str | None = None) -> None:
        self.calls: list[tuple[str, list[str] | None, int]] = []
        self.unsupported_document_id = unsupported_document_id

    def search(self, question, document_ids=None, top_k=6):
        self.calls.append((question, document_ids, top_k))
        document_id = document_ids[0]
        if document_id == self.unsupported_document_id:
            return []
        return [
            EvidenceAnchor(
                evidence_id=f"local-{index}",
                document_id=document_id,
                document_title=None,
                page_number=index,
                block_ids=[f"{document_id}-b{index}"],
                bbox=[10, 20, 300, 80],
                section="Method",
                quote=f"Evidence {index} from {document_id}",
                score=0.95 - index * 0.05,
                retrieval_mode="reranked",
            )
            for index in (1, 2)
        ]


def _document(session: Session, suffix: str, title: str) -> Document:
    document = Document(
        original_filename=f"{suffix}.pdf",
        storage_key=f"comparison-{suffix}.pdf",
        size_bytes=100,
        sha256=(suffix * 64)[:64],
        status=DocumentStatus.READY,
        title=title,
    )
    session.add(document)
    session.commit()
    return document


@pytest.mark.asyncio
async def test_comparison_balances_retrieval_and_validates_cross_document_claims() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    provider = RecordingComparisonProvider()
    retriever = BalancedRetriever()
    with Session(engine, expire_on_commit=False) as session:
        first = _document(session, "a", "Transformer")
        second = _document(session, "b", "BERT")
        result = await PaperComparisonService(
            session,
            retriever=retriever,
            provider=provider,  # type: ignore[arg-type]
        ).compare([first, second], "比较两篇论文", evidence_per_document=2)

    assert [call[1] for call in retriever.calls] == [[first.id], [second.id]]
    assert [item.evidence_id for item in result.evidence] == ["E1", "E2", "E3", "E4"]
    assert [item.evidence_count for item in result.papers] == [2, 2]
    assert [item.relation for item in result.claims] == [
        "agreement",
        "difference",
        "single_source",
        "unclassified",
    ]
    assert result.claims[0].document_ids == [first.id, second.id]
    assert result.stats.agreement_count == 1
    assert result.stats.difference_count == 1
    assert result.stats.conflict_count == 0
    assert result.stats.supported_document_count == 2
    assert any("缺少第二篇论文证据" in item for item in result.warnings)
    assert "absence of a fact" in provider.questions[0]


@pytest.mark.asyncio
async def test_comparison_stops_when_fewer_than_two_papers_have_evidence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    provider = RecordingComparisonProvider()
    with Session(engine, expire_on_commit=False) as session:
        first = _document(session, "c", "Paper C")
        second = _document(session, "d", "Paper D")
        result = await PaperComparisonService(
            session,
            retriever=BalancedRetriever(unsupported_document_id=second.id),
            provider=provider,  # type: ignore[arg-type]
        ).compare([first, second], "比较结果", evidence_per_document=2)

    assert result.insufficient_evidence is True
    assert result.claims == []
    assert result.stats.supported_document_count == 1
    assert result.papers[1].coverage == "no_evidence"
    assert provider.questions == []


def test_comparison_api_returns_source_aware_claims(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = RecordingComparisonProvider()
    retriever = BalancedRetriever()
    with Session(engine, expire_on_commit=False) as session:
        first = _document(session, "e", "Paper E")
        second = _document(session, "f", "Paper F")
        service = PaperComparisonService(
            session,
            retriever=retriever,
            provider=provider,  # type: ignore[arg-type]
        )
        monkeypatch.setattr(agent_routes, "PaperComparisonService", lambda _: service)
        app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/agent/compare",
                    json={
                        "question": "对比论文贡献",
                        "document_ids": [first.id, second.id],
                        "evidence_per_document": 2,
                    },
                )
                duplicate_response = client.post(
                    "/api/v1/agent/compare",
                    json={
                        "question": "对比论文贡献",
                        "document_ids": [first.id, first.id],
                    },
                )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["claims"][0]["relation"] == "agreement"
    assert len(response.json()["papers"]) == 2
    assert duplicate_response.status_code == 422
