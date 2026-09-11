from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.models.recommendation import ArxivSubscription, PaperRecommendation
from app.schemas.discovery import PaperCandidate
from app.services.github_code import GitHubCodeSearchService
from app.services.recommendations import RecommendationService


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path,
        "upload_dir": tmp_path / "uploads",
        "parsed_dir": tmp_path / "parsed",
        "translation_dir": tmp_path / "translations",
        "embedding_provider": "hash",
        "embedding_dimensions": 384,
        "github_code_search_enabled": True,
        "github_code_lookup_limit": 3,
    }
    values.update(overrides)
    return Settings(**values)


def _candidate(**overrides) -> PaperCandidate:
    values = {
        "source": "arxiv",
        "source_id": "2609.01234v1",
        "title": "Grounded Retrieval for Scientific Documents",
        "authors": ["Ada Researcher"],
        "abstract": "A retrieval augmented generation system for scientific PDF documents.",
        "year": 2026,
        "published_at": datetime.now(UTC).isoformat(),
        "venue": "cs.IR, cs.CL",
        "arxiv_id": "2609.01234",
        "arxiv_version": 1,
        "landing_url": "https://arxiv.org/abs/2609.01234v1",
        "pdf_url": "https://arxiv.org/pdf/2609.01234v1.pdf",
        "importable": True,
    }
    values.update(overrides)
    return PaperCandidate(**values)


@pytest.mark.asyncio
async def test_github_search_returns_title_aligned_repository(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/repositories"
        assert "Scientific Documents" in request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "name": "grounded-retrieval-scientific-documents",
                        "full_name": "lab/grounded-retrieval-scientific-documents",
                        "description": "Official scientific PDF retrieval implementation",
                        "html_url": "https://github.com/lab/grounded-retrieval-scientific-documents",
                        "stargazers_count": 321,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = GitHubCodeSearchService(_settings(tmp_path), client)
        match = await service.find_repository(_candidate())

    assert match is not None
    assert match.stars == 321
    assert match.url.startswith("https://github.com/lab/")


@pytest.mark.asyncio
async def test_refresh_persists_ranked_recommendation_and_preserves_feedback(tmp_path) -> None:
    class FakeDiscovery:
        async def search_arxiv(self, **kwargs):
            assert kwargs == {
                "query": "scientific RAG",
                "category": "cs.IR",
                "limit": 10,
            }
            return [_candidate()]

    class FakeCodeSearch:
        async def find_repository(self, candidate):
            from app.services.github_code import GitHubRepositoryMatch

            return GitHubRepositoryMatch(
                url="https://github.com/lab/scientific-rag",
                full_name="lab/scientific-rag",
                stars=500,
                score=0.8,
            )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    service = RecommendationService(FakeDiscovery(), FakeCodeSearch(), _settings(tmp_path))
    with Session(engine) as session:
        subscription = ArxivSubscription(
            name="Scientific RAG",
            query="scientific RAG",
            category="cs.IR",
            max_results=10,
        )
        session.add(subscription)
        session.add(
            Document(
                original_filename="local.pdf",
                storage_key="local.pdf",
                size_bytes=100,
                sha256="c" * 64,
                status=DocumentStatus.READY,
                title="Retrieval Augmented Generation for Scientific Papers",
            )
        )
        session.commit()

        first = await service.refresh(session, subscription.id)
        recommendation = session.scalar(select(PaperRecommendation))
        assert recommendation is not None
        recommendation.feedback = "like"
        session.commit()
        second = await service.refresh(session, subscription.id)
        session.refresh(recommendation)

    assert first.error is None
    assert first.discovered == 1
    assert second.updated == 1
    assert recommendation.feedback == "like"
    assert recommendation.code_stars == 500
    assert recommendation.final_score > 0.5
    assert "GitHub 代码 500★" in recommendation.recommendation_reason


@pytest.mark.asyncio
async def test_refresh_records_provider_failure_on_subscription(tmp_path) -> None:
    class FailingDiscovery:
        async def search_arxiv(self, **kwargs):
            raise RuntimeError("arXiv unavailable")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    service = RecommendationService(
        FailingDiscovery(),
        GitHubCodeSearchService(_settings(tmp_path)),
        _settings(tmp_path),
    )
    with Session(engine) as session:
        subscription = ArxivSubscription(name="Test", query="agents", max_results=5)
        session.add(subscription)
        session.commit()
        summary = await service.refresh(session, subscription.id)
        session.refresh(subscription)

    assert summary.error == "arXiv unavailable"
    assert subscription.last_error == "arXiv unavailable"
