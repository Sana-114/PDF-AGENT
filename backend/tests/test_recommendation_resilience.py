"""Synthetic boundary cases, separate from live arXiv acceptance."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.models.recommendation import ArxivSubscription, PaperRecommendation
from app.schemas.discovery import PaperCandidate
from app.schemas.recommendation import ArxivSubscriptionCreate
from app.services.recommendations import RecommendationService


def candidate(version=1, **changes):
    return PaperCandidate(
        **{
            "source": "arxiv",
            "source_id": f"2609.01234v{version}",
            "arxiv_id": "2609.01234",
            "arxiv_version": version,
            "title": f"Public Synthetic Retrieval v{version}",
            "authors": ["Synthetic Author"],
            "landing_url": f"https://arxiv.org/abs/2609.01234v{version}",
            "pdf_url": f"https://arxiv.org/pdf/2609.01234v{version}",
            "published_at": datetime.now(UTC).isoformat(),
            **changes,
        }
    )


class Feed:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    async def search_arxiv(self, **_):
        self.calls += 1
        if isinstance(self.items, Exception):
            raise self.items
        return self.items


@pytest.fixture
def workspace(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        subscription = ArxivSubscription(name="Synthetic", query="retrieval", max_results=5)
        db.add(subscription)
        db.commit()
        config = Settings(_env_file=None, embedding_provider="hash", github_code_lookup_limit=0)
        yield db, subscription, config


@pytest.mark.asyncio
@pytest.mark.parametrize("versions", [[1, 1], [1, 2], [2, 1]])
async def test_duplicate_feed_is_one_record_and_keeps_latest(workspace, versions):
    db, sub, config = workspace
    feed = Feed([candidate(version) for version in versions])
    result = await RecommendationService(discovery=feed, config=config).refresh(db, sub.id)
    assert result.error is None
    rows = db.scalars(select(PaperRecommendation)).all()
    assert len(rows) == result.discovered == 1
    assert result.updated == 0
    assert rows[0].arxiv_version == max(versions)
    assert feed.calls == 1


@pytest.mark.asyncio
async def test_late_older_feed_does_not_replace_newer_or_feedback(workspace):
    db, sub, config = workspace
    feed = Feed([candidate(3)])
    service = RecommendationService(discovery=feed, config=config)
    await service.refresh(db, sub.id)
    paper = db.scalar(select(PaperRecommendation))
    paper.feedback = "like"
    db.commit()
    feed.items = [candidate(1)]
    result = await service.refresh(db, sub.id)
    db.refresh(paper)
    assert result.error is None
    assert paper.arxiv_version == 3 and paper.title.endswith("v3")
    assert paper.feedback == "like" and paper.pdf_url.endswith("v3")


@pytest.mark.asyncio
async def test_failed_write_rolls_back_batch_and_next_refresh_recovers(workspace, monkeypatch):
    db, sub, config = workspace
    feed = Feed([candidate(1)])
    service = RecommendationService(discovery=feed, config=config)
    await service.refresh(db, sub.id)
    paper = db.scalar(select(PaperRecommendation))
    before_time, paper_id = sub.last_refreshed_at, paper.id
    persist = service._persist

    def broken_write(*args):
        result = persist(*args)
        db.add(
            PaperRecommendation(
                id=paper_id,
                subscription_id=sub.id,
                arxiv_id="synthetic-duplicate",
                title="Must roll back",
                landing_url="https://arxiv.org/abs/2609.01234",
                pdf_url="https://arxiv.org/pdf/2609.01234",
                recommendation_reason="synthetic fault",
            )
        )
        return result

    feed.items = [candidate(2)]
    monkeypatch.setattr(service, "_persist", broken_write)
    result = await service.refresh(db, sub.id)
    assert result.error
    db.expire_all()
    assert db.get(PaperRecommendation, paper_id).arxiv_version == 1
    assert db.get(ArxivSubscription, sub.id).last_refreshed_at == before_time
    assert db.get(ArxivSubscription, sub.id).last_error
    monkeypatch.setattr(service, "_persist", persist)
    recovered = await service.refresh(db, sub.id)
    assert recovered.error is None and recovered.updated == 1
    assert db.get(ArxivSubscription, sub.id).last_error is None
    assert feed.calls == 3  # Seed, one failing attempt, one explicit recovery.


@pytest.mark.asyncio
async def test_empty_feed_is_success_without_embedding_call(workspace, monkeypatch):
    import app.services.recommendations as module

    db, sub, config = workspace
    called = []

    def forbidden(*_):
        called.append(True)
        raise AssertionError("Empty feed must not call an embedding provider")

    monkeypatch.setattr(module, "get_embedding_provider", forbidden)
    result = await RecommendationService(discovery=Feed([]), config=config).refresh(db, sub.id)
    assert result.error is None and result.discovered == 0 and result.updated == 0
    assert result.ranking_provider is None and called == []
    assert sub.last_refreshed_at is not None


@pytest.mark.parametrize("name", [" ", "\t\n"])
def test_blank_subscription_name_rejected(name):
    with pytest.raises(ValueError):
        ArxivSubscriptionCreate(name=name, query="retrieval")


@pytest.mark.asyncio
async def test_network_failure_retains_previous_recommendations_and_recovers(workspace):
    db, sub, config = workspace
    feed = Feed([candidate()])
    service = RecommendationService(discovery=feed, config=config)
    await service.refresh(db, sub.id)
    paper = db.scalar(select(PaperRecommendation))
    paper.feedback = "dislike"
    db.commit()
    feed.items = RuntimeError("Synthetic provider unavailable")
    failed = await service.refresh(db, sub.id)
    assert failed.error == "Synthetic provider unavailable"
    assert db.scalar(select(PaperRecommendation)).feedback == "dislike"
    feed.items = [candidate(2)]
    recovered = await service.refresh(db, sub.id)
    assert recovered.error is None and recovered.updated == 1
    assert sub.last_error is None and paper.feedback == "dislike"


def test_periodic_batch_continues_after_one_job_failure(workspace, monkeypatch):
    from app.workers import tasks

    db, sub, _ = workspace
    second = ArxivSubscription(name="Second", query="agents", active=True)
    inactive = ArxivSubscription(name="Inactive", query="ignored", active=False)
    db.add_all([second, inactive])
    db.commit()
    ids = {sub.id, second.id}
    calls = []

    def refresh(identifier):
        calls.append(identifier)
        if len(calls) == 1:
            raise RuntimeError("https://user:secret@example.invalid")
        return {"subscription_id": identifier, "discovered": 1, "updated": 0, "error": None}

    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: Session(db.get_bind()))
    monkeypatch.setattr(tasks, "refresh_arxiv_subscription", refresh)
    monkeypatch.setattr(tasks.settings, "arxiv_request_delay_seconds", 0)
    result = tasks.refresh_all_arxiv_subscriptions()
    assert set(calls) == ids and result["refreshed"] == 2
    assert result["results"][0]["error"] and result["results"][1]["error"] is None
    assert "secret" not in str(result) and "https://" not in str(result)
