from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import agent as agent_routes
from app.api.routes import documents as document_routes
from app.llm.base import (
    GeneratedSegmentTranslations,
    GeneratedTranslation,
    LLMConfigurationError,
)
from app.llm.extractive import ExtractiveProvider
from app.main import app
from app.models.document import DocumentStatus


class StubTranslationProvider:
    name = "stub"
    model = "academic-translator"

    async def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> GeneratedTranslation:
        assert text == "注意力机制保留公式 beta_2 = 0.98。"
        assert source_language == "auto"
        assert target_language == "en"
        return GeneratedTranslation(text="The attention mechanism preserves beta_2 = 0.98.")

    async def translate_segments(
        self,
        segments: list[tuple[str, str]],
        source_language: str,
        target_language: str,
    ) -> GeneratedSegmentTranslations:
        assert source_language == "auto"
        assert target_language == "zh"
        return GeneratedSegmentTranslations(
            items={block_id: f"译文：{text}" for block_id, text in segments}
        )


def test_translate_endpoint_returns_provider_result(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_routes,
        "get_llm_provider",
        lambda: StubTranslationProvider(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/translate",
            json={
                "text": "注意力机制保留公式 beta_2 = 0.98。",
                "source_language": "auto",
                "target_language": "en",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "translation": "The attention mechanism preserves beta_2 = 0.98.",
        "source_language": "auto",
        "target_language": "en",
        "provider": "stub",
        "model": "academic-translator",
    }


def test_translate_endpoint_rejects_same_source_and_target() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/translate",
            json={"text": "text", "source_language": "en", "target_language": "en"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_extractive_provider_explicitly_rejects_translation() -> None:
    with pytest.raises(LLMConfigurationError, match="需要生成式 LLM"):
        await ExtractiveProvider().translate_text("text", "auto", "zh")


def test_page_translation_endpoint_preserves_block_ids_and_bbox(monkeypatch) -> None:
    monkeypatch.setattr(
        document_routes,
        "_get_document",
        lambda document_id, db: SimpleNamespace(id=document_id, status=DocumentStatus.READY),
    )
    monkeypatch.setattr(
        document_routes,
        "read_parsed_document",
        lambda _: {
            "pages": [
                {
                    "page_number": 2,
                    "blocks": [
                        {"block_id": "p2-b1", "text": "Attention", "bbox": [1, 2, 3, 4]},
                        {"block_id": "p2-b2", "text": "Results", "bbox": [5, 6, 7, 8]},
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        document_routes,
        "get_llm_provider",
        lambda: StubTranslationProvider(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/documents/doc-1/translations/pages/2",
            json={"target_language": "zh"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page_number"] == 2
    assert payload["target_language"] == "zh"
    assert payload["segments"] == [
        {
            "block_id": "p2-b1",
            "bbox": [1.0, 2.0, 3.0, 4.0],
            "source_text": "Attention",
            "translation": "译文：Attention",
        },
        {
            "block_id": "p2-b2",
            "bbox": [5.0, 6.0, 7.0, 8.0],
            "source_text": "Results",
            "translation": "译文：Results",
        },
    ]
