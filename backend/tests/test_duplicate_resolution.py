import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.routes import documents as document_routes
from app.core.database import Base
from app.models.chunk import DocumentChunk
from app.models.document import Document, DocumentStatus
from app.schemas.document import DuplicateResolutionRequest


def _document(document_id: str, version: int) -> Document:
    return Document(
        id=document_id,
        original_filename=f"1706.03762v{version}.pdf",
        storage_key=f"{document_id}.pdf",
        content_type="application/pdf",
        size_bytes=1000 + version,
        sha256=str(version) * 64,
        status=DocumentStatus.READY,
        title="Attention Is All You Need",
        page_count=15,
        arxiv_id="1706.03762",
        arxiv_version=version,
    )


def _session_with_version_conflict() -> tuple[Session, Document, Document]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    existing = _document("version-7", 7)
    current = _document("version-1", 1)
    current.duplicate_of_id = existing.id
    current.duplicate_score = 0.94
    current.duplicate_recommendation = "库中已有更新的 arXiv v7，建议保留。"
    session.add_all([existing, current])
    session.commit()
    return session, current, existing


def _disable_asset_deletion(monkeypatch) -> list[tuple[str, str]]:
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(document_routes, "delete_document_index_safely", lambda _: None)
    monkeypatch.setattr(
        document_routes.storage,
        "delete",
        lambda storage_key, document_id: deleted.append((storage_key, document_id)),
    )
    return deleted


@pytest.mark.parametrize("foreign_keys", [False, True])
@pytest.mark.parametrize("action", ["delete", "keep_existing", "replace_existing"])
def test_all_removal_paths_clean_chunks_even_without_sqlite_cascades(
    monkeypatch, foreign_keys, action
):
    session, current, existing = _session_with_version_conflict()
    _disable_asset_deletion(monkeypatch)
    session.connection().exec_driver_sql("PRAGMA foreign_keys=" + ("ON" if foreign_keys else "OFF"))
    for document in (current, existing):
        session.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=0,
                page_number=1,
                block_ids_json='["body"]',
                text="public test body",
                char_count=16,
            )
        )
    session.commit()
    removed = existing if action == "replace_existing" else current
    survivor = current if removed is existing else existing
    if action == "delete":
        document_routes.delete_document(removed.id, session)
    else:
        document_routes.resolve_document_duplicate(
            current.id,
            DuplicateResolutionRequest(action=action),
            session,
        )
    assert (
        session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == removed.id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == survivor.id)
        )
        == 1
    )
    session.close()


def test_keep_existing_removes_newly_uploaded_duplicate(monkeypatch) -> None:
    session, current, existing = _session_with_version_conflict()
    deleted = _disable_asset_deletion(monkeypatch)

    result = document_routes.resolve_document_duplicate(
        current.id,
        DuplicateResolutionRequest(action="keep_existing"),
        session,
    )

    assert result.kept_document.id == existing.id
    assert result.removed_document_id == current.id
    assert session.get(Document, current.id) is None
    assert session.get(Document, existing.id) is not None
    assert deleted == [(current.storage_key, current.id)]
    session.close()


def test_replace_existing_removes_old_version_and_clears_warning(monkeypatch) -> None:
    session, current, existing = _session_with_version_conflict()
    deleted = _disable_asset_deletion(monkeypatch)

    result = document_routes.resolve_document_duplicate(
        current.id,
        DuplicateResolutionRequest(action="replace_existing"),
        session,
    )

    kept = session.get(Document, current.id)
    assert result.kept_document.id == current.id
    assert result.removed_document_id == existing.id
    assert kept is not None
    assert kept.duplicate_of_id is None
    assert kept.duplicate_score is None
    assert kept.duplicate_recommendation is None
    assert session.get(Document, existing.id) is None
    assert deleted == [(existing.storage_key, existing.id)]
    session.close()


def test_keep_both_versions_clears_pending_warning(monkeypatch) -> None:
    session, current, existing = _session_with_version_conflict()
    deleted = _disable_asset_deletion(monkeypatch)

    result = document_routes.resolve_document_duplicate(
        current.id,
        DuplicateResolutionRequest(action="keep_both"),
        session,
    )

    kept = session.get(Document, current.id)
    assert result.removed_document_id is None
    assert kept is not None and kept.duplicate_of_id is None
    assert session.get(Document, existing.id) is not None
    assert deleted == []
    session.close()
