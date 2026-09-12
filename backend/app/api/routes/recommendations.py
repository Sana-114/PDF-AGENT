import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.recommendation import ArxivSubscription, PaperRecommendation
from app.schemas.recommendation import (
    ArxivSubscriptionCreate,
    ArxivSubscriptionRead,
    RecommendationFeedbackRequest,
    RecommendationList,
    RecommendationRead,
    RecommendationRefreshRead,
    SubscriptionList,
)
from app.workers.tasks import refresh_arxiv_subscription

router = APIRouter()


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _recommendation_read(item: PaperRecommendation) -> RecommendationRead:
    return RecommendationRead(
        id=item.id,
        subscription_id=item.subscription_id,
        arxiv_id=item.arxiv_id,
        arxiv_version=item.arxiv_version,
        title=item.title,
        authors=_json_list(item.authors_json),
        abstract=item.abstract,
        categories=_json_list(item.categories_json),
        published_at=item.published_at,
        landing_url=item.landing_url,
        pdf_url=item.pdf_url,
        code_url=item.code_url,
        code_stars=item.code_stars,
        relevance_score=item.relevance_score,
        freshness_score=item.freshness_score,
        final_score=item.final_score,
        recommendation_reason=item.recommendation_reason,
        feedback=item.feedback,
        discovered_at=item.discovered_at,
        updated_at=item.updated_at,
    )


@router.post(
    "/subscriptions",
    response_model=ArxivSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_subscription(
    request: ArxivSubscriptionCreate,
    db: Annotated[Session, Depends(get_db)],
) -> ArxivSubscriptionRead:
    subscription = ArxivSubscription(
        name=request.name.strip(),
        query=request.query,
        category=request.category,
        max_results=request.max_results,
        active=request.active,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    if request.refresh_now:
        try:
            refresh_arxiv_subscription.delay(subscription.id)
            db.refresh(subscription)
        except Exception as exc:
            subscription.last_error = f"刷新任务投递失败：{exc}"[:2000]
            db.commit()
    return ArxivSubscriptionRead.model_validate(subscription)


@router.get("/subscriptions", response_model=SubscriptionList)
def list_subscriptions(db: Annotated[Session, Depends(get_db)]) -> SubscriptionList:
    items = db.scalars(
        select(ArxivSubscription).order_by(ArxivSubscription.created_at.desc())
    ).all()
    return SubscriptionList(
        items=[ArxivSubscriptionRead.model_validate(item) for item in items]
    )


@router.post(
    "/subscriptions/{subscription_id}/refresh",
    response_model=RecommendationRefreshRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def refresh_subscription(
    subscription_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> RecommendationRefreshRead:
    if db.get(ArxivSubscription, subscription_id) is None:
        raise HTTPException(status_code=404, detail="arXiv 订阅不存在。")
    try:
        refresh_arxiv_subscription.delay(subscription_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"刷新任务投递失败：{exc}") from exc
    return RecommendationRefreshRead(subscription_id=subscription_id, status="queued")


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(
    subscription_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    subscription = db.get(ArxivSubscription, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="arXiv 订阅不存在。")
    db.execute(
        sql_delete(PaperRecommendation).where(
            PaperRecommendation.subscription_id == subscription_id
        )
    )
    db.delete(subscription)
    db.commit()


@router.get("", response_model=RecommendationList)
def list_recommendations(
    db: Annotated[Session, Depends(get_db)],
    subscription_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> RecommendationList:
    filters = []
    if subscription_id:
        filters.append(PaperRecommendation.subscription_id == subscription_id)
    total = db.scalar(
        select(func.count()).select_from(PaperRecommendation).where(*filters)
    ) or 0
    items = db.scalars(
        select(PaperRecommendation)
        .where(*filters)
        .order_by(PaperRecommendation.final_score.desc(), PaperRecommendation.published_at.desc())
        .limit(limit)
    ).all()
    return RecommendationList(items=[_recommendation_read(item) for item in items], total=total)


@router.post("/{recommendation_id}/feedback", response_model=RecommendationRead)
def set_recommendation_feedback(
    recommendation_id: str,
    request: RecommendationFeedbackRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RecommendationRead:
    recommendation = db.get(PaperRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="推荐论文不存在。")
    recommendation.feedback = request.feedback
    db.commit()
    db.refresh(recommendation)
    return _recommendation_read(recommendation)
