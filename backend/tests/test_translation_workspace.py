from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_db() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _create_glossary(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/writing/glossaries",
        json={
            "name": "RAG 核心术语",
            "source_language": "zh",
            "target_language": "en",
            "terms": [
                {"source": "检索增强生成", "target": "retrieval-augmented generation"},
                {"source": "大语言模型", "target": "large language model"},
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_glossary_crud_and_language_direction_validation(client: TestClient) -> None:
    created = _create_glossary(client)
    assert created["term_count"] == 2
    assert created["terms"][0]["source"] == "检索增强生成"

    duplicate = client.post(
        "/api/v1/writing/glossaries",
        json={
            "name": "RAG 核心术语",
            "source_language": "zh",
            "target_language": "en",
            "terms": [{"source": "向量检索", "target": "vector retrieval"}],
        },
    )
    assert duplicate.status_code == 409

    invalid = client.post(
        "/api/v1/writing/glossaries",
        json={
            "name": "无效方向",
            "source_language": "zh",
            "target_language": "zh",
            "terms": [{"source": "论文", "target": "论文"}],
        },
    )
    assert invalid.status_code == 422

    updated = client.put(
        f"/api/v1/writing/glossaries/{created['id']}",
        json={
            "name": "RAG 术语（审定）",
            "source_language": "zh",
            "target_language": "en",
            "terms": [{"source": "向量检索", "target": "vector retrieval"}],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["term_count"] == 1

    listed = client.get("/api/v1/writing/glossaries")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["items"]] == ["RAG 术语（审定）"]


def test_translation_draft_review_is_invalidated_after_edit(client: TestClient) -> None:
    glossary = _create_glossary(client)
    created = client.post(
        "/api/v1/writing/drafts",
        json={
            "title": "RAG 摘要译稿",
            "source_text": "本文提出一种检索增强生成方法。",
            "translated_text": "This paper proposes a retrieval-augmented generation method.",
            "source_language": "zh",
            "target_language": "en",
            "document_type": "abstract",
            "glossary_id": glossary["id"],
            "provider": "stub",
            "model": "academic-translator",
        },
    )
    assert created.status_code == 201
    draft_id = created.json()["id"]

    reviewed = client.patch(
        f"/api/v1/writing/drafts/{draft_id}", json={"status": "reviewed"}
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"
    assert reviewed.json()["reviewed_at"] is not None

    edited = client.patch(
        f"/api/v1/writing/drafts/{draft_id}",
        json={"translated_text": "This paper presents a revised RAG method."},
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "draft"
    assert edited.json()["reviewed_at"] is None

    listed = client.get("/api/v1/writing/drafts")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["translated_text"].endswith("method.")

    deleted_glossary = client.delete(f"/api/v1/writing/glossaries/{glossary['id']}")
    assert deleted_glossary.status_code == 204
    refreshed_draft = client.get("/api/v1/writing/drafts").json()["items"][0]
    assert refreshed_draft["glossary_id"] is None

    deleted_draft = client.delete(f"/api/v1/writing/drafts/{draft_id}")
    assert deleted_draft.status_code == 204
    assert client.get("/api/v1/writing/drafts").json()["total"] == 0


def test_translation_draft_rejects_missing_glossary(client: TestClient) -> None:
    response = client.post(
        "/api/v1/writing/drafts",
        json={
            "title": "测试译稿",
            "source_text": "中文原文",
            "translated_text": "English translation",
            "source_language": "zh",
            "target_language": "en",
            "document_type": "abstract",
            "glossary_id": "missing-glossary",
        },
    )
    assert response.status_code == 409
    assert "术语库不存在" in response.json()["detail"]
