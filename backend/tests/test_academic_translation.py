import pytest
from fastapi.testclient import TestClient

from app.api.routes import writing as writing_routes
from app.llm.base import GeneratedSegmentTranslations, LLMResponseError
from app.main import app
from app.schemas.academic_translation import AcademicTranslationRequest
from app.services.academic_translation import AcademicTranslationService


class AcademicTranslationProvider:
    name = "stub"
    model = "academic-translator"
    supports_translation = True

    def __init__(self) -> None:
        self.contexts: list[str | None] = []

    async def translate_segments(
        self,
        segments: list[tuple[str, str]],
        source_language: str,
        target_language: str,
        context: str | None = None,
    ) -> GeneratedSegmentTranslations:
        assert source_language == "zh"
        assert target_language == "en"
        self.contexts.append(context)
        return GeneratedSegmentTranslations(
            items={
                block_id: text.replace("本文提出", "This paper proposes").replace(
                    "方法", " method"
                ).replace("准确率为", "The accuracy is ").replace(
                    "代码", "The code"
                ).replace("保持不变", "remains unchanged")
                for block_id, text in segments
            }
        )


@pytest.mark.asyncio
async def test_academic_translation_preserves_protected_content_and_glossary() -> None:
    provider = AcademicTranslationProvider()
    request = AcademicTranslationRequest(
        text=(
            "本文提出检索增强生成方法 $y=x^2$，准确率为 91.5% [11]。\n\n"
            "代码 `beta_2 = 0.98` 保持不变。"
        ),
        glossary=[{"source": "检索增强生成", "target": "retrieval-augmented generation"}],
    )

    result = await AcademicTranslationService(provider).translate(request)

    assert "retrieval-augmented generation" in result.translation
    assert "$y=x^2$" in result.translation
    assert "91.5%" in result.translation
    assert "[11]" in result.translation
    assert "`beta_2 = 0.98`" in result.translation
    assert result.paragraph_count == 2
    assert result.request_count == 1
    assert result.glossary_applied[0].count == 1
    assert {item.kind for item in result.preservation_checks} == {
        "formula",
        "number",
        "citation",
        "code",
    }
    assert provider.contexts and "journal-style abstract" in (provider.contexts[0] or "")


@pytest.mark.asyncio
async def test_long_translation_batches_paragraphs_without_reordering() -> None:
    provider = AcademicTranslationProvider()
    request = AcademicTranslationRequest(
        text="\n\n".join("本文提出方法" for _ in range(41)),
        document_type="paper",
    )

    result = await AcademicTranslationService(provider).translate(request)

    assert result.paragraph_count == 41
    assert result.request_count == 2
    assert result.translation.count("This paper proposes") == 41
    assert all("formal academic prose" in (context or "") for context in provider.contexts)


@pytest.mark.asyncio
async def test_translation_rejects_missing_protected_placeholder() -> None:
    class DroppingProvider(AcademicTranslationProvider):
        async def translate_segments(
            self,
            segments: list[tuple[str, str]],
            source_language: str,
            target_language: str,
            context: str | None = None,
        ) -> GeneratedSegmentTranslations:
            del source_language, target_language, context
            return GeneratedSegmentTranslations(
                items={
                    block_id: text.replace("`__PAPERPILOT_A__`", "")
                    for block_id, text in segments
                }
            )

    request = AcademicTranslationRequest(text="公式为 $y=x$。")
    with pytest.raises(LLMResponseError, match="未完整保留"):
        await AcademicTranslationService(DroppingProvider()).translate(request)


def test_academic_translation_api_returns_quality_report(monkeypatch) -> None:
    provider = AcademicTranslationProvider()
    monkeypatch.setattr(writing_routes, "get_llm_provider", lambda: provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/writing/translate",
            json={
                "text": "本文提出检索增强生成方法，准确率为 90%。",
                "source_language": "zh",
                "target_language": "en",
                "document_type": "abstract",
                "glossary": [
                    {"source": "检索增强生成", "target": "retrieval-augmented generation"}
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "stub"
    assert "retrieval-augmented generation" in payload["translation"]
    assert payload["preservation_checks"] == [{"kind": "number", "count": 1}]


def test_academic_translation_api_requires_generative_provider(monkeypatch) -> None:
    class DisabledProvider:
        name = "extractive"
        model = None
        supports_translation = False

    monkeypatch.setattr(writing_routes, "get_llm_provider", lambda: DisabledProvider())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/writing/translate",
            json={"text": "中文摘要", "source_language": "zh", "target_language": "en"},
        )

    assert response.status_code == 503
    assert "生成式 LLM" in response.json()["detail"]
