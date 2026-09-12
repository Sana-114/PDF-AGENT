import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.embeddings.factory import get_embedding_provider
from app.embeddings.hashing import HashEmbeddingProvider
from app.models.document import Document, DocumentStatus
from app.models.recommendation import ArxivSubscription, PaperRecommendation
from app.schemas.discovery import PaperCandidate
from app.services.github_code import (
    GitHubCodeSearchError,
    GitHubCodeSearchService,
    github_code_search,
)
from app.services.paper_discovery import PaperDiscoveryService, paper_discovery


@dataclass(slots=True)
class RefreshSummary:
    subscription_id: str
    discovered: int
    updated: int
    error: str | None = None


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _freshness(published_at: str | None) -> float:
    if not published_at:
        return 0.35
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - published).total_seconds() / 86400)
    except ValueError:
        return 0.35
    return _clamp(math.exp(-age_days / 365.0))


class RecommendationService:
    def __init__(
        self,
        discovery: PaperDiscoveryService = paper_discovery,
        code_search: GitHubCodeSearchService = github_code_search,
        config: Settings = settings,
    ) -> None:
        self.discovery = discovery
        self.code_search = code_search
        self.config = config

    async def refresh(self, db: Session, subscription_id: str) -> RefreshSummary:
        subscription = db.get(ArxivSubscription, subscription_id)
        if subscription is None:
            return RefreshSummary(subscription_id=subscription_id, discovered=0, updated=0)
        try:
            candidates = await self.discovery.search_arxiv(
                query=subscription.query,
                category=subscription.category,
                limit=subscription.max_results,
            )
            await self._attach_code(candidates)
            vectors = await self._recommendation_vectors(db, subscription, candidates)
            discovered, updated = self._persist(db, subscription, candidates, vectors)
            subscription.last_refreshed_at = datetime.now(UTC)
            subscription.last_error = None
            db.commit()
            return RefreshSummary(subscription.id, discovered, updated)
        except Exception as exc:
            subscription.last_error = str(exc)[:2000]
            db.commit()
            return RefreshSummary(subscription.id, 0, 0, str(exc))

    async def _attach_code(self, candidates: list[PaperCandidate]) -> None:
        lookups = 0
        for candidate in candidates:
            if candidate.code_url:
                continue
            if lookups >= self.config.github_code_lookup_limit:
                break
            lookups += 1
            try:
                match = await self.code_search.find_repository(candidate)
            except GitHubCodeSearchError:
                break
            if match:
                candidate.code_url = match.url
                candidate.code_stars = match.stars

    async def _recommendation_vectors(
        self,
        db: Session,
        subscription: ArxivSubscription,
        candidates: list[PaperCandidate],
    ) -> tuple[list[list[float]], list[float]]:
        document_titles = db.scalars(
            select(Document.title)
            .where(Document.status == DocumentStatus.READY, Document.title.is_not(None))
            .limit(80)
        ).all()
        liked = db.scalars(
            select(PaperRecommendation).where(PaperRecommendation.feedback == "like").limit(30)
        ).all()
        disliked = db.scalars(
            select(PaperRecommendation).where(PaperRecommendation.feedback == "dislike").limit(30)
        ).all()
        profile = "\n".join(
            filter(
                None,
                [
                    subscription.query,
                    subscription.category or "",
                    *document_titles,
                    *(item.title for item in liked),
                ],
            )
        )
        negative_profile = "\n".join(item.title for item in disliked)
        candidate_texts = [
            f"{item.title}\n{item.abstract or ''}\n{item.venue or ''}" for item in candidates
        ]
        texts = [profile or subscription.name, negative_profile, *candidate_texts]
        try:
            provider = get_embedding_provider(self.config)
            vectors = await asyncio.to_thread(provider.embed_dense, texts)
        except Exception:
            fallback = HashEmbeddingProvider(dimensions=384)
            vectors = fallback.embed_dense(texts)
        profile_vector, negative_vector, *candidate_vectors = vectors
        scores = [
            _clamp((_similarity(profile_vector, vector) + 1.0) / 2.0)
            for vector in candidate_vectors
        ]
        negative_scores = [
            _clamp((_similarity(negative_vector, vector) + 1.0) / 2.0)
            if negative_profile
            else 0.0
            for vector in candidate_vectors
        ]
        return candidate_vectors, [
            _clamp(score - 0.12 * negative)
            for score, negative in zip(scores, negative_scores, strict=False)
        ]

    def _persist(
        self,
        db: Session,
        subscription: ArxivSubscription,
        candidates: list[PaperCandidate],
        vectors: tuple[list[list[float]], list[float]],
    ) -> tuple[int, int]:
        _, relevance_scores = vectors
        existing = {
            item.arxiv_id: item
            for item in db.scalars(
                select(PaperRecommendation).where(
                    PaperRecommendation.subscription_id == subscription.id
                )
            ).all()
        }
        discovered = 0
        updated = 0
        for candidate, relevance in zip(candidates, relevance_scores, strict=False):
            if not candidate.arxiv_id or not candidate.pdf_url or not candidate.landing_url:
                continue
            freshness = _freshness(candidate.published_at)
            code_bonus = 1.0 if candidate.code_url else 0.0
            final_score = _clamp(0.62 * relevance + 0.28 * freshness + 0.10 * code_bonus)
            recommendation = existing.get(candidate.arxiv_id)
            if recommendation is None:
                recommendation = PaperRecommendation(
                    subscription_id=subscription.id,
                    arxiv_id=candidate.arxiv_id,
                    title=candidate.title,
                    landing_url=candidate.landing_url,
                    pdf_url=candidate.pdf_url,
                    recommendation_reason="",
                )
                db.add(recommendation)
                discovered += 1
            else:
                updated += 1
            recommendation.arxiv_version = candidate.arxiv_version
            recommendation.title = candidate.title
            recommendation.authors_json = json.dumps(candidate.authors, ensure_ascii=False)
            recommendation.abstract = candidate.abstract
            recommendation.categories_json = json.dumps(
                [item.strip() for item in (candidate.venue or "").split(",") if item.strip()],
                ensure_ascii=False,
            )
            recommendation.published_at = candidate.published_at
            recommendation.landing_url = candidate.landing_url
            recommendation.pdf_url = candidate.pdf_url
            recommendation.code_url = candidate.code_url
            recommendation.code_stars = candidate.code_stars
            recommendation.relevance_score = round(relevance, 4)
            recommendation.freshness_score = round(freshness, 4)
            recommendation.final_score = round(final_score, 4)
            reasons = [f"与本地研究兴趣相关度 {relevance:.0%}", f"时效性 {freshness:.0%}"]
            if candidate.code_url:
                code_label = (
                    f"GitHub 代码 {candidate.code_stars}★"
                    if candidate.code_stars
                    else "附 GitHub 代码"
                )
                reasons.append(code_label)
            recommendation.recommendation_reason = "；".join(reasons)
            recommendation.metadata_json = json.dumps(
                candidate.model_dump(mode="json"), ensure_ascii=False
            )
        return discovered, updated


recommendation_service = RecommendationService()
