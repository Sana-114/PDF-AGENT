import json

import httpx
import pytest

from app.llm.base import LLMTransientError
from app.llm.openai_responses import OpenAIResponsesProvider


@pytest.mark.asyncio
async def test_blank_timeout_exception_has_actionable_reason_and_only_one_call():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("", request=request)

    provider = OpenAIResponsesProvider(
        api_key="test-placeholder",
        model="test",
        base_url="https://example.test",
        timeout_seconds=12,
        provider_name="deepseek",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMTransientError, match="ReadTimeout.*12 秒"):
        await provider._post_response({"model": "test"})
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ['{"answer":"", "claims":[', '{"answer":"", "claims":[]}'])
async def test_incomplete_generation_keeps_diagnostics_and_never_retries(output):
    from app.llm.base import LLMResponseError

    calls = []
    provider = OpenAIResponsesProvider(
        api_key="test-placeholder",
        model="test",
        base_url="https://example.test",
        timeout_seconds=12,
    )

    async def interrupted(payload):
        calls.append(payload)
        assert "Return answer as an empty string" in payload["instructions"]
        return {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 100},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": output}]}],
        }

    provider._post_response = interrupted
    with pytest.raises(LLMResponseError, match="输出未完成") as error:
        await provider.generate_grounded_answer("Explain the two stages.", [])
    assert error.value.raw_grounded_output["unparsed_text"] == output
    assert error.value.raw_grounded_output["response_status"] == "incomplete"
    assert error.value.raw_grounded_output["incomplete_details"] == {"reason": "max_output_tokens"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_english_outline_prompt_does_not_trigger_chinese_answer_heuristic():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.models.document import Document, DocumentStatus
    from app.schemas.agent import AgentTraceStep, AskResponse
    from app.services.writing_outline import WritingOutlineService

    class FailedAgent:
        async def ask(self, request, **kwargs):
            assert "Respond in English" in request.question
            assert not any("\u4e00" <= c <= "\u9fff" for c in request.question)
            return AskResponse(
                answer="",
                claims=[],
                evidence=[],
                provider="extractive",
                insufficient_evidence=True,
                trace=[
                    AgentTraceStep(
                        skill="generate_answer",
                        status="fallback",
                        summary="ReadTimeout: cloud request timed out",
                        duration_ms=0,
                    )
                ],
            )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        doc = Document(
            id="test",
            original_filename="test.pdf",
            storage_key="test.pdf",
            sha256="0" * 64,
            size_bytes=1,
            status=DocumentStatus.READY,
        )
        db.add(doc)
        db.commit()
        result = await WritingOutlineService(db, agent=FailedAgent()).generate(
            "Study parameter efficient adaptation", [doc], language="en"
        )
    assert any("ReadTimeout" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_explicit_english_output_takes_precedence_over_chinese_research_idea():
    provider = OpenAIResponsesProvider(
        api_key="test-placeholder",
        model="test",
        base_url="https://example.test",
        timeout_seconds=12,
    )

    async def inspect_payload(payload):
        assert "requested output language is English" in payload["instructions"]
        assert "必须用中文回答" not in payload["instructions"]
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"answer": "No evidence", "claims": []}),
                        }
                    ],
                }
            ]
        }

    provider._post_response = inspect_payload
    await provider.generate_grounded_answer("Research idea: 低秩适配. Respond in English.", [])
