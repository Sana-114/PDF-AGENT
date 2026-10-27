from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.models.recommendation import ArxivSubscription
from app.schemas.discovery import PaperCandidate
from app.services.recommendations import RecommendationService


@pytest.mark.asyncio
async def test_new_subscription_never_embeds_missing_negative_profile():
    calls = []

    class RejectBlankEmbedding:
        def embed_dense(self, texts):
            assert all(text.strip() for text in texts)
            calls.append(texts)
            return [[1.0, 0.0] for _ in texts]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        subscription = ArxivSubscription(name="Research", query="low-rank adaptation")
        db.add(subscription)
        db.commit()
        candidate = PaperCandidate(
            source="arxiv",
            source_id="2106.09685",
            arxiv_id="2106.09685",
            title="LoRA",
            pdf_url="https://arxiv.org/pdf/2106.09685",
            landing_url="https://arxiv.org/abs/2106.09685",
        )
        service = RecommendationService(config=Settings())
        with patch(
            "app.services.recommendations.get_embedding_provider",
            return_value=RejectBlankEmbedding(),
        ):
            vectors = await service._recommendation_vectors(db, subscription, [candidate])
        assert len(calls) == 1 and len(calls[0]) == 2
        assert vectors[1] == [1.0]
        service._persist(db, subscription, [candidate], vectors)
        db.commit()
        from sqlalchemy import select

        from app.models.recommendation import PaperRecommendation

        recommendation = db.scalar(select(PaperRecommendation))
        assert "发布时间未知" in recommendation.recommendation_reason


@pytest.mark.asyncio
async def test_semantic_failure_is_visible_in_summary_and_persisted_api_metadata():
    from sqlalchemy import select

    from app.api.routes.recommendations import _recommendation_read
    from app.models.recommendation import PaperRecommendation

    candidate = PaperCandidate(
        source="arxiv",
        source_id="2106.09685",
        arxiv_id="2106.09685",
        title="LoRA",
        pdf_url="https://arxiv.org/pdf/2106.09685",
        landing_url="https://arxiv.org/abs/2106.09685",
    )

    class Discovery:
        async def search_arxiv(self, **kwargs):
            return [candidate]

    class FailingEmbedding:
        def embed_dense(self, texts):
            raise RuntimeError("connection reset")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    config = Settings(embedding_provider="openai-compatible", github_code_lookup_limit=0)
    service = RecommendationService(discovery=Discovery(), config=config)
    with (
        Session(engine) as db,
        patch(
            "app.services.recommendations.get_embedding_provider", return_value=FailingEmbedding()
        ),
    ):
        subscription = ArxivSubscription(name="Research", query="low-rank adaptation")
        db.add(subscription)
        db.commit()
        summary = await service.refresh(db, subscription.id)
        item = db.scalar(select(PaperRecommendation))
        result = _recommendation_read(item)
        assert summary.error is None  # feed retrieval succeeded, ranking degraded
        assert summary.ranking_provider == result.ranking_provider == "hash"
        assert "不代表 BGE" in summary.ranking_warning
        assert result.ranking_warning == summary.ranking_warning
        assert result.ranking_warning in result.recommendation_reason


def test_legacy_recommendation_metadata_stays_readable():
    from datetime import UTC, datetime

    from app.api.routes.recommendations import _recommendation_read
    from app.models.recommendation import PaperRecommendation

    for metadata in ('{"source":"arxiv"}', "[]", "invalid json"):
        item = PaperRecommendation(
            id="legacy",
            subscription_id="subscription",
            arxiv_id="2106.09685",
            title="LoRA",
            landing_url="https://arxiv.org/abs/2106.09685",
            pdf_url="https://arxiv.org/pdf/2106.09685",
            recommendation_reason="legacy",
            metadata_json=metadata,
            authors_json="[]",
            categories_json="[]",
            feedback="neutral",
            relevance_score=0.5,
            freshness_score=0.35,
            final_score=0.4,
            discovered_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        result = _recommendation_read(item)
        assert result.ranking_provider is None
        assert result.ranking_warning is None
