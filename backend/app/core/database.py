from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_db() -> None:
    # Import models here so SQLAlchemy has registered their tables.
    from app.models.chunk import DocumentChunk  # noqa: F401
    from app.models.document import Document  # noqa: F401
    from app.models.paper_source import PaperSource  # noqa: F401

    Base.metadata.create_all(bind=engine)
