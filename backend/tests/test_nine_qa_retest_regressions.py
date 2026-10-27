"""Held-out synthetic values for defects exposed by the unchanged nine-question run."""

import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.schemas.agent import AskRequest, EvidenceAnchor
from app.services.answer_completeness import affirmative_model_values, contract_prompt
from app.services.table_comparison import remove_table_bleu_refusals, table_bleu_claims

TABLE = """Table: BLEU evaluation results
| | BLEU | | Training Cost (FLOPs) | |
| --- | --- | --- | --- | --- |
| Model | | | | |
| | EN-DE | EN-FR | EN-DE | EN-FR |
| Prior Single | 17 | 25 | 400 | 800 |
| Prior Ensemble | 18 | 26 | 500 | 900 |
| Example (base model) | 19 | 27 | 600 | 1000 |
| Example (big) | 20 | 28 | 700 | 1100 |
"""
QUESTION = "Example (big) English-to-German BLEU 分数、旧最佳及提升是多少？"


def anchor(quote, key="E1", kind="table", doc="source"):
    return EvidenceAnchor(evidence_id=key, document_id=doc, page_number=1,
                          quote=quote, source_type=kind, score=1)


class Registry:
    def __init__(self, evidence):
        self.evidence = evidence

    async def execute(self, *_):
        return self.evidence


class Provider:
    name, model = "test", "synthetic"

    def __init__(self, text):
        self.text, self.calls, self.question = text, 0, None

    async def generate_grounded_answer(self, question, evidence):
        self.calls += 1
        self.question = question
        return GeneratedAnswer(answer="unchecked narrative", claims=[
            GeneratedClaim(text=self.text, evidence_ids=["E1"])])


@pytest.mark.parametrize("text", [
    "关于 GPT-1 的参数量：根据后续论文推断出 GPT-1 基线为60M，非原论文直接报告。",
    "GPT-1 参数量可依据后续表格推断，其基线为60M。",
    "GPT-1 parameter count is inferred from the later paper; its baseline is 60M.",
])
def test_labelled_parameter_baseline_assignment_is_not_mistaken_for_a_missing_field(text):
    assert affirmative_model_values(text, "model_parameters").get("GPT-1") == {
        "quantity:60000000"}


@pytest.mark.parametrize("text", [
    "GPT-1 基线为60M，无法确认其参数量。",
    "GPT-1 的训练 token 基线为60M，参数量另见其他资料。",
    "GPT-1 的基线为60M tokens，参数量未给出。",
    "GPT-1 的基线为60M。",
])
def test_baseline_without_parameter_label_or_with_training_or_refusal_does_not_fill_field(text):
    assert not affirmative_model_values(text, "model_parameters")


@pytest.mark.parametrize("refusal", [
    "证据中未明确给出此前最佳 English-to-German BLEU 数值，无法精确计算提升量。",
    "EN-DE 的旧最佳 BLEU 未知。",
    "无法计算 EN-DE 的精确 BLEU 差值。",
    "The previous best EN-DE BLEU is unknown; an exact improvement cannot be calculated.",
])
@pytest.mark.asyncio
async def test_complete_table_proof_cannot_be_published_with_a_conflicting_refusal(refusal):
    provider = Provider(refusal)
    result = await ResearchAgent(None, registry=Registry([anchor(TABLE)]), provider=provider).ask(
        AskRequest(question=QUESTION))
    assert provider.calls == 1
    assert not result.insufficient_evidence
    assert "20 - 18 = 2" in result.answer
    assert all(claim.text != refusal for claim in result.claims)
    assert any(step.skill == "audit_table_answer_consistency" and step.status == "rejected"
               for step in result.trace)


@pytest.mark.parametrize("statement", [
    "English-to-French 的旧最佳 BLEU 未知，无法计算差值。",
    "GPU 型号无法确定。",
    "BLEU 提升不是原文直接报告的数字，而是基于表格计算。",
    "该段文字没有给出此前最佳 BLEU 数值。",
])
@pytest.mark.asyncio
async def test_other_task_refusals_and_provenance_are_not_deleted_by_a_table_proof(statement):
    result = await ResearchAgent(None, registry=Registry([anchor(TABLE)]),
                                 provider=Provider(statement)).ask(AskRequest(question=QUESTION))
    assert any(claim.text == statement for claim in result.claims)


@pytest.mark.asyncio
async def test_no_table_proof_means_the_refusal_is_preserved_and_the_answer_remains_incomplete():
    refusal = "旧最佳 BLEU 未知，无法计算精确差值。"
    result = await ResearchAgent(None, registry=Registry([anchor("Missing metric headers")]),
                                 provider=Provider(refusal)).ask(AskRequest(question=QUESTION))
    assert result.insufficient_evidence
    assert any(claim.text == refusal for claim in result.claims)


@pytest.mark.asyncio
async def test_ambiguous_conflicting_tables_do_not_suppress_a_legitimate_refusal():
    refusal = "旧最佳 BLEU 未知，无法计算精确差值。"
    evidence = [anchor(TABLE), anchor(TABLE.replace("| 20 |", "| 21 |"), "E2", doc="other")]
    result = await ResearchAgent(None, registry=Registry(evidence), provider=Provider(refusal)).ask(
        AskRequest(question=QUESTION))
    assert result.insufficient_evidence
    assert any(claim.text == refusal for claim in result.claims)


def test_an_incompletely_published_proof_cannot_suppress_a_refusal():
    from app.schemas.agent import AnswerClaim

    proof = table_bleu_claims(QUESTION, [anchor(TABLE)])
    refusal = AnswerClaim(text="EN-DE 旧最佳 BLEU 未知。", evidence_ids=["E1"])
    partial = [refusal, *[AnswerClaim(text=item.text, evidence_ids=item.evidence_ids)
                         for item in proof[:2]]]
    assert remove_table_bleu_refusals(QUESTION, partial, proof) == (partial, 0)


def test_a_refusal_citing_an_unrelated_passage_is_not_removed_by_this_bounded_table_guard():
    from app.schemas.agent import AnswerClaim

    proof = table_bleu_claims(QUESTION, [anchor(TABLE)])
    refusal = AnswerClaim(text="EN-DE 旧最佳 BLEU 未知。", evidence_ids=["E2"])
    claims = [refusal, *[AnswerClaim(text=item.text, evidence_ids=item.evidence_ids)
                        for item in proof]]
    assert remove_table_bleu_refusals(QUESTION, claims, proof) == (claims, 0)


def test_context_prompt_highlights_existing_comparison_ids_without_inserting_an_answer():
    question = "GPT-1 和 GPT-2 的上下文窗口？"
    source = anchor("The model follows a named baseline. We increase the context size "
                    "from 640 to 1280 tokens.", "E7", "text")
    prompt = contract_prompt(question, [source])
    assert "Source-reading priority" in prompt and "E7" in prompt
    assert "640" not in prompt and "1280" not in prompt
    assert "ambiguous" in prompt and "incomplete" in prompt
    assert contract_prompt(question) in prompt


@pytest.mark.parametrize("quote, kind", [
    ("We train on sequences of 640 tokens.", "text"),
    ("Sample contexts are 640 tokens, and completions are 1280 tokens.", "table"),
    ("The model follows a baseline, with 1280-dimensional hidden states.", "text"),
    ("The model follows a baseline. Context size increases from 640 to 1280 tokens.", "reference"),
])
def test_training_lengths_captions_dimensions_and_references_do_not_create_comparison_guidance(
    quote, kind,
):
    assert "Source-reading priority" not in contract_prompt(
        "GPT-1 到 GPT-2 上下文窗口？", [anchor(quote, kind=kind)])


def test_context_guidance_does_not_leak_into_other_or_historical_questions():
    source = anchor("The model follows a baseline. Context size increases from 640 to 1280 tokens.",
                    kind="text")
    assert "Source-reading priority" not in contract_prompt("GPT-1 和 GPT-2 参数量？", [source])
    question = "Prior dialogue: GPT-1 上下文\n\nCurrent user question:\n作者是谁？"
    assert contract_prompt(question, [source]) == question
