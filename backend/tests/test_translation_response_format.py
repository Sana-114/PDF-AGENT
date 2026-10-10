import json

import httpx
import pytest

from app.llm.base import LLMResponseError
from app.llm.deepseek_responses import DeepSeekResponsesProvider


def provider_for(text: str) -> DeepSeekResponsesProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "Return only a JSON object" in payload["instructions"]
        assert "protected placeholder tokens exactly" in payload["instructions"]
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": text[:len(text) // 2]},
            {"type": "output_text", "text": text[len(text) // 2:]},
        ]}]})

    return DeepSeekResponsesProvider(
        api_key="test-key", model="deepseek-flash", base_url="https://llm.test",
        timeout_seconds=10, transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ['{"translation":"新架构"}', '```json\n{"translation":"新架构"}\n```']
)
async def test_translation_accepts_complete_json_and_joins_output_parts(text: str) -> None:
    result = await provider_for(text).translate_text("a new architecture", "en", "zh")
    assert result.text == "新架构"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    '新架构', 'Explanation: {"translation":"新架构"}', '[]',
    '{"translation":null}', '{"translation":123}', '{"translation":""}',
    '{"translation":"新架构","extra":"invented"}',
])
async def test_translation_rejects_prose_and_invalid_fields(text: str) -> None:
    with pytest.raises(LLMResponseError):
        await provider_for(text).translate_text("a new architecture", "en", "zh")


@pytest.mark.asyncio
async def test_segment_translation_accepts_fenced_complete_json() -> None:
    text = '```json\n{"translations":[{"block_id":"p1-b1","translation":"新架构"}]}\n```'
    result = await provider_for(text).translate_segments(
        [("p1-b1", "a new architecture")], "en", "zh"
    )
    assert result.items == {"p1-b1": "新架构"}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    '{"translations":null}', '{"translations":[null]}',
    '{"translations":[{"block_id":"p1-b1","translation":null}]}',
])
async def test_segment_translation_rejects_invalid_schema(text: str) -> None:
    with pytest.raises(LLMResponseError):
        await provider_for(text).translate_segments([("p1-b1", "text")], "en", "zh")
