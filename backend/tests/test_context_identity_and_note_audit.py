import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import GeneratedAnswer
from app.llm.model_fields import render_numeric_fields, requested_numeric_fields
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.services.answer_completeness import affirmative_model_values, missing_fields


@pytest.mark.parametrize("chinese", [True, False])
@pytest.mark.parametrize("kind", ["model_context", "model_parameters"])
def test_unavailable_note_numbers_are_not_an_affirmative_answer(chinese, kind):
    question = "GPT-1 上下文窗口多少？" if kind == "model_context" else "GPT-1 参数量多少？"
    fields = requested_numeric_fields(question)
    note = ("GPT-1 的数值不可确认。另一比较的 context size from 256 to 1536 tokens "
            "或 parameters 70M 450M 不足以证明。")
    claim = render_numeric_fields({next(iter(fields)): {
        "status": "unavailable", "value": None, "source_note": note,
        "evidence_ids": ["E1"],
    }}, fields, {"E1"}, chinese=chinese)[0]
    assert note in claim.text
    assert affirmative_model_values(claim.text, kind) == {}
    evidence = [EvidenceAnchor(evidence_id="E1", document_id="public", page_number=1,
                              quote=note, score=1)]
    assert missing_fields(question, [AnswerClaim(text=claim.text,
                         evidence_ids=claim.evidence_ids)], evidence)


def test_supported_note_cannot_give_its_baseline_value_to_current_model():
    text = ("GPT-2 的上下文窗口为 1536 tokens（来源说明：GPT-2 (AuthorY et al., 2023) "
            "报告 context size from 256 to 1536 tokens；原 GPT-1 基线为 256 tokens。）。")
    assert affirmative_model_values(text, "model_context") == {"GPT-2": {"1536"}}
    assert affirmative_model_values("GPT-2 参数量为450M（约0.45B参数）。",
                                    "model_parameters") == {"GPT-2": {"quantity:450000000"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("context,score", [(True, 0.8), (True, 0.01), (False, 0.8)])
async def test_diversification_preserves_only_a_scored_body_cited_context_identity(context, score):
    body = "The model follows Model-Z (AuthorX et al., 2022). Context size from 256 to 1536 tokens."

    class Registry:
        async def execute(self, name, args, context):
            document = args["document_ids"][0]
            return [EvidenceAnchor(evidence_id=f"E{number}", document_id=document,
                    page_number=1, quote=quote, source_type=source, score=value)
                    for number, (quote, source, value) in enumerate([
                        (body, "text", 1),
                        ("AuthorX. A source title. 2022.", "reference", score),
                        ("AuthorY. An unrelated title. 2022.", "reference", 1),
                        ("AuthorX. A second matching entry. 2022.", "reference", 1),
                    ], 1)]

    class Provider:
        name, model = "test", "test"
        received = None

        async def generate_grounded_answer(self, question, evidence):
            self.received = evidence
            return GeneratedAnswer(answer="", claims=[])

    provider = Provider()
    question = "GPT-1 和 GPT-2 上下文窗口？" if context else "GPT-1 和 GPT-2 参数量？"
    await ResearchAgent(None, registry=Registry(), provider=provider).ask(AskRequest(
        question=question, document_ids=["A", "B"], top_k=6))
    refs = [item for item in provider.received if item.source_type == "reference"]
    # A weak first match does not cause a score boost: the genuinely strong
    # second matching entry may fill the bounded identity slot instead.
    assert len(refs) == (2 if context else 0)
    assert all(item.score >= 0.18 and "AuthorX" in item.quote for item in refs)
    assert all("unrelated" not in item.quote for item in provider.received)
    assert len(provider.received) <= 6
    assert len({item.evidence_id for item in provider.received}) == len(provider.received)
