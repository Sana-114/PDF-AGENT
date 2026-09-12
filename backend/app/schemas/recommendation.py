from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArxivSubscriptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    query: str = Field(default="", max_length=500)
    category: str | None = Field(default=None, max_length=64)
    max_results: int = Field(default=10, ge=1, le=30)
    active: bool = True
    refresh_now: bool = True

    @model_validator(mode="after")
    def require_filter(self):
        self.query = " ".join(self.query.split())
        self.category = self.category.strip() if self.category else None
        if not self.query and not self.category:
            raise ValueError("关键词和 arXiv 分类至少填写一项。")
        return self


class ArxivSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    query: str
    category: str | None
    max_results: int
    active: bool
    last_refreshed_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class SubscriptionList(BaseModel):
    items: list[ArxivSubscriptionRead]


class RecommendationRead(BaseModel):
    id: str
    subscription_id: str
    arxiv_id: str
    arxiv_version: int | None
    title: str
    authors: list[str]
    abstract: str | None
    categories: list[str]
    published_at: str | None
    landing_url: str
    pdf_url: str
    code_url: str | None
    code_stars: int | None
    relevance_score: float
    freshness_score: float
    final_score: float
    recommendation_reason: str
    feedback: Literal["neutral", "like", "dislike"]
    discovered_at: datetime
    updated_at: datetime


class RecommendationList(BaseModel):
    items: list[RecommendationRead]
    total: int


class RecommendationFeedbackRequest(BaseModel):
    feedback: Literal["neutral", "like", "dislike"]


class RecommendationRefreshRead(BaseModel):
    subscription_id: str
    status: Literal["queued"]
