import pytest
from pydantic import BaseModel

from app.agent.registry import SkillContext, SkillDefinition, SkillRegistry
from app.llm.extractive import ExtractiveProvider
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

