import pytest
from fastapi.testclient import TestClient

from app.api.routes import agent as agent_routes
from app.llm.base import GeneratedTranslation, LLMConfigurationError
from app.llm.extractive import ExtractiveProvider
from app.main import app


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
