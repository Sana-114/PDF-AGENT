import json

import httpx
import pytest
from pydantic import BaseModel

from app.agent.registry import SkillContext, SkillDefinition, SkillRegistry
from app.core.config import Settings
from app.llm.deepseek_responses import DeepSeekResponsesProvider
from app.llm.extractive import ExtractiveProvider
from app.llm.factory import get_llm_provider
from app.llm.openai_responses import OpenAIResponsesProvider
from app.schemas.agent import EvidenceAnchor


class EchoInput(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_skill_registry_validates_and_executes() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="echo",
            description="Echo a value.",
            input_model=EchoInput,
            handler=lambda payload, _: {"value": payload.value},
        )
    )

    result = await registry.execute("echo", {"value": "ok"}, SkillContext(db=None))  # type: ignore[arg-type]

    assert result == {"value": "ok"}
    assert registry.list()[0].public_dict()["name"] == "echo"


@pytest.mark.asyncio
async def test_extractive_provider_never_creates_unanchored_claims() -> None:
    provider = ExtractiveProvider()
    evidence = EvidenceAnchor(
        evidence_id="E1",
        document_id="doc-1",
        page_number=3,
        block_ids=["p3-b2"],
        quote="The optimizer used beta_1 = 0.9 and beta_2 = 0.98.",
        score=0.9,
    )

    result = await provider.generate_grounded_answer("What beta values were used?", [evidence])

    assert result.claims[0].evidence_ids == ["E1"]
    assert "[E1]" in result.answer


def test_openai_output_text_extraction() -> None:
    response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"answer":"ok","claims":[]}'}],
            }
        ]
    }

    assert OpenAIResponsesProvider._extract_output_text(response) == (
        '{"answer":"ok","claims":[]}'
    )


def test_deepseek_factory_uses_responses_compatibility_mode() -> None:
    provider = get_llm_provider(
        Settings(
            llm_provider="deepseek",
            llm_model="deepseek-flash",
            llm_api_key="test-key",
            llm_base_url="https://api.deepseek.com",
        )
    )

    assert isinstance(provider, DeepSeekResponsesProvider)
    assert provider.name == "deepseek"
    assert provider.model == "deepseek-flash"
    assert provider.strict_structured_output is False
    assert provider.reasoning_effort == "none"


@pytest.mark.asyncio
async def test_deepseek_translation_uses_supported_response_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        output_format = payload["text"]["format"]
        assert request.url == "https://api.deepseek.com/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["model"] == "deepseek-flash"
        assert payload["reasoning"] == {"effort": "none"}
        assert "store" not in payload
        assert output_format["type"] == "json_schema"
        assert output_format["name"] == "academic_translation"
        assert "strict" not in output_format
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"translation":"神经网络"}',
                            }
                        ],
                    }
                ]
            },
        )

    provider = DeepSeekResponsesProvider(
        api_key="test-key",
        model="deepseek-flash",
        base_url="https://api.deepseek.com/",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.translate_text("neural network", "en", "zh")

    assert result.text == "神经网络"
