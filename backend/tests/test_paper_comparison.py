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
from app.services.paper_comparison import (
    PaperComparisonService,
    _comparison_facets,
    _select_faceted_evidence,
    _unsupported_numeric_facts,
)


def test_comparison_facets_split_explicit_research_dimensions() -> None:
    assert _comparison_facets(
        "Compare GPT papers: decoder architecture, parameter counts, "
        "context window lengths, and layer-normalization placement."
    ) == [
        "decoder architecture",
        "parameter counts",
        "context window lengths",
        "layer-normalization placement",
    ]
    assert _comparison_facets("Compare two papers generally") == []


def test_faceted_selection_keeps_context_evidence_when_general_rank_misses_it() -> None:
    general = [
        EvidenceAnchor(
            evidence_id="local",
            chunk_id=f"general-{index}",
            document_id="paper",
            page_number=index,
            quote="generic architecture evidence",
            score=0.95 - index * 0.01,
        )
        for index in range(1, 5)
    ]
    context = EvidenceAnchor(
        evidence_id="local",
        chunk_id="context-window",
        document_id="paper",
        page_number=8,
        quote="All models use a context window of nctx = 2048 tokens.",
        score=0.8,
    )

    selected = _select_faceted_evidence([[general[0]], [context]], [*general, context], 2)

    assert [item.chunk_id for item in selected] == ["general-1", "context-window"]


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
                    text="[AGREEMENT][DIMENSION: 注意力机制] 两篇论文都使用注意力机制。",
                    evidence_ids=["E1", "E3"],
                ),
                GeneratedClaim(
                    text="[DIFFERENCE][DIMENSION: 训练设置] 两篇论文采用不同训练设置。",
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


class NumericComparisonProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    async def generate_grounded_answer(self, question, evidence):
        del question
        assert [item.evidence_id for item in evidence] == ["E1", "E2"]
        return GeneratedAnswer(
            answer="未经校验的总述声称模型包含 999 billion 参数。",
            claims=[
                GeneratedClaim(
                    text=(
                        "[DIFFERENCE][DIMENSION: 网络深度] "
                        "Paper A uses 12 layers, while Paper B uses 24 layers."
                    ),
                    evidence_ids=["E1", "E2"],
                ),
                GeneratedClaim(
                    text="[CONFLICT][DIMENSION: 参数量] The models contain 175 billion parameters.",
                    evidence_ids=["E1", "E2"],
                ),
                GeneratedClaim(
                    text="[AGREEMENT][DIMENSION: 架构] Both models use an encoder.",
                    evidence_ids=["E1", "E999"],
                ),
            ],
        )


class NumericRetriever:
    def __init__(self, quotes: dict[str, str]) -> None:
        self.quotes = quotes

    def search(self, question, document_ids=None, top_k=6):
        del question, top_k
        document_id = document_ids[0]
        return [
            EvidenceAnchor(
                evidence_id="local",
                document_id=document_id,
                document_title=None,
                page_number=3,
                block_ids=[f"{document_id}-depth"],
                bbox=[10, 20, 300, 80],
                section="Architecture",
                quote=self.quotes[document_id],
                score=0.95,
                retrieval_mode="reranked",
            )
        ]


class DiversityProvider:
    name = "deepseek"
    model = "deepseek-flash"
    supports_translation = True

    async def generate_grounded_answer(self, question, evidence):
        del question
        midpoint = len(evidence) // 2
        return GeneratedAnswer(
            answer="raw answer",
            claims=[
                GeneratedClaim(
                    text="[AGREEMENT][DIMENSION: 方法] Both papers report grounded results.",
                    evidence_ids=[evidence[0].evidence_id, evidence[midpoint].evidence_id],
                )
            ],
        )


class DiversityRetriever:
    def search(self, question, document_ids=None, top_k=6):
        del question, top_k
        document_id = document_ids[0]
        fixtures = [
            ("duplicate", 1, "text", "Method", "grounded method evidence", 0.99),
            ("duplicate", 1, "text", "Method", "grounded method evidence", 0.98),
            ("same-page", 1, "text", "Method", "grounded method detail", 0.97),
            ("table", 2, "table", "Table 1", "grounded result table", 0.90),
            ("formula", 3, "formula", "Equation 2", "grounded objective formula", 0.85),
        ]
        return [
            EvidenceAnchor(
                evidence_id=f"local-{index}",
                chunk_id=f"{document_id}-{key}",
                document_id=document_id,
                document_title=None,
                page_number=page,
                block_ids=[f"{document_id}-{key}"],
                bbox=[10, 20, 300, 80],
                section=section,
                source_type=source_type,
                quote=quote,
                score=score,
                retrieval_mode="reranked",
            )
            for index, (key, page, source_type, section, quote, score) in enumerate(
                fixtures, start=1
            )
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
    assert [item.dimension for item in result.claims[:2]] == ["注意力机制", "训练设置"]
    assert result.matrix[0].cells[0].status == "cited"
    assert result.matrix[0].cells[1].status == "cited"
    assert result.audit.generated_claim_count == 4
    assert result.audit.accepted_claim_count == 4
    assert result.audit.published_claim_citation_coverage == 1.0
    assert result.audit.cross_document_claim_count == 3
    assert result.stats.agreement_count == 1
    assert result.stats.difference_count == 1
    assert result.stats.conflict_count == 0
    assert result.stats.supported_document_count == 2
    assert any("缺少第二篇论文证据" in item for item in result.warnings)
    assert "absence of a fact" in provider.questions[0]
    assert "final published answer will be reconstructed" in provider.questions[0]


@pytest.mark.asyncio
async def test_comparison_rebuilds_answer_and_rejects_uncited_numeric_facts() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        first = _document(session, "numeric-a", "Paper A")
        second = _document(session, "numeric-b", "Paper B")
        retriever = NumericRetriever(
            {
                first.id: "The encoder contains 12 layers.",
                second.id: "The encoder contains 24 layers.",
            }
        )
        result = await PaperComparisonService(
            session,
            retriever=retriever,
            provider=NumericComparisonProvider(),  # type: ignore[arg-type]
        ).compare([first, second], "Compare network depth", evidence_per_document=1)

    assert len(result.claims) == 1
    assert result.claims[0].dimension == "网络深度"
    assert "12 layers" in result.answer
    assert "999" not in result.answer
    assert "175" not in result.answer
    assert result.audit.generated_claim_count == 3
    assert result.audit.accepted_claim_count == 1
    assert result.audit.rejected_claim_count == 2
    assert result.audit.rejection_reasons == [
        "unsupported_numeric_fact",
        "invalid_or_missing_evidence_id",
    ]
    assert result.audit.referenced_document_count == 2
    assert result.audit.evidence_utilization == 1.0
    assert result.matrix[0].cells[0].evidence_ids == ["E1"]
    assert result.matrix[0].cells[1].evidence_ids == ["E2"]
    assert any("原文证据中不存在的数值" in item for item in result.warnings)


@pytest.mark.asyncio
async def test_comparison_selects_cross_page_and_source_type_evidence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        first = _document(session, "diverse-a", "Paper A")
        second = _document(session, "diverse-b", "Paper B")
        result = await PaperComparisonService(
            session,
            retriever=DiversityRetriever(),
            provider=DiversityProvider(),  # type: ignore[arg-type]
        ).compare([first, second], "compare grounded results", evidence_per_document=3)

    assert [paper.candidate_count for paper in result.papers] == [5, 5]
    assert [paper.evidence_count for paper in result.papers] == [3, 3]
    assert [paper.evidence_page_count for paper in result.papers] == [3, 3]
    assert result.papers[0].source_types == ["text", "table", "formula"]
    assert [item.page_number for item in result.evidence[:3]] == [1, 2, 3]


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
    assert response.json()["matrix"][0]["cells"][0]["status"] == "cited"
    assert response.json()["audit"]["published_claim_citation_coverage"] == 1.0
    assert duplicate_response.status_code == 422


def test_numeric_fact_validation_uses_exact_tokens() -> None:
    evidence = [
        EvidenceAnchor(
            evidence_id="E1",
            document_id="paper-a",
            document_title="Model with 175B parameters",
            chunk_id="chunk-a",
            quote="The model has 312 layers.",
            page_number=4,
            block_ids=["b-4"],
            score=0.9,
            source_type="text",
        )
    ]

    assert _unsupported_numeric_facts("The model has 12 layers.", evidence) == ["12"]
    assert _unsupported_numeric_facts("The model has 175 billion parameters.", evidence) == []
