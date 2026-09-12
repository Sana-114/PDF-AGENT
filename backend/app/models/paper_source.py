from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class PaperSource(Base):
    """Provenance for a document fetched from an external scholarly index."""

    __tablename__ = "paper_sources"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(256), index=True)
    doi: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
