from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ArxivSubscription(Base):
    __tablename__ = "arxiv_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(160))
    query: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_results: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PaperRecommendation(Base):
    __tablename__ = "paper_recommendations"
    __table_args__ = (
        UniqueConstraint("subscription_id", "arxiv_id", name="uq_subscription_arxiv_paper"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("arxiv_subscriptions.id", ondelete="CASCADE"), index=True
    )
    arxiv_id: Mapped[str] = mapped_column(String(64), index=True)
    arxiv_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    authors_json: Mapped[str] = mapped_column(Text, default="[]")
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories_json: Mapped[str] = mapped_column(Text, default="[]")
    published_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    landing_url: Mapped[str] = mapped_column(Text)
    pdf_url: Mapped[str] = mapped_column(Text)
    code_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    freshness_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    recommendation_reason: Mapped[str] = mapped_column(Text)
    feedback: Mapped[str] = mapped_column(String(16), default="neutral", index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
