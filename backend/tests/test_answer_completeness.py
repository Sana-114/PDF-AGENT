import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.services.answer_completeness import (
    contract_prompt,
    current_question,
    missing_fields,
    requested_models,
)
from app.services.grounded_text import checked_claims
from app.services.table_comparison import table_bleu_claims


def anchor(quote, evidence_id="E1", document_id="paper", source_type="text"):
    return EvidenceAnchor(evidence_id=evidence_id, document_id=document_id, page_number=8,
                          quote=quote, source_type=source_type, score=1)


TABLE = """Table: BLEU evaluation results
| | BLEU | | Training Cost (FLOPs) | |
| --- | --- | --- | --- | --- |
| Model | | | | |
| | EN-DE | EN-FR | EN-DE | EN-FR |
| Prior Single | 25.16 | 40.46 | 123 | 456 |
| Prior Ensemble | 26.36 | 41.29 | 999 | 888 |
| Transformer (base model) | 27.3 | 38.1 | 321 |  |
| Transformer (big) | 28.4 | 41.0 | | 654 |
"""
BLEU_QUESTION = "Transformer (big) English-to-German BLEU 分数，旧最佳和提升分别是多少？"


def test_bleu_compares_correct_task_column_including_ensembles_not_flops():
    claims = table_bleu_claims(BLEU_QUESTION, [anchor(TABLE, source_type="table")])
    assert len(claims) == 3
    assert "26.36" in claims[1].text
    assert "28.4 - 26.36 = 2.04" in claims[2].text
    checked, rejected = checked_claims(claims, [anchor(TABLE, source_type="table")])
    assert len(checked) == 3 and rejected == 0
    french = table_bleu_claims(BLEU_QUESTION.replace("German", "French"),
                              [anchor(TABLE, source_type="table")])
    assert "41.0 - 41.29 = -0.29" in french[2].text


@pytest.mark.parametrize("question", [
    "Transformer (big) BLEU 提升多少？",
    "Transformer (big) English-to-German 和 English-to-French BLEU 提升多少？",
    "Transformer English-to-German BLEU 提升多少？",
])
def test_ambiguous_table_comparison_does_not_guess(question):
    assert table_bleu_claims(question, [anchor(TABLE, source_type="table")]) == []


def test_conflicting_versions_or_missing_metric_headers_are_not_silently_selected():
    assert table_bleu_claims(BLEU_QUESTION, [
        anchor(TABLE, source_type="table"),
        anchor(TABLE.replace("28.4", "28.5"), "E2", "different-paper", "table"),
    ]) == []
    assert table_bleu_claims(BLEU_QUESTION, [anchor(TABLE.replace("BLEU", "Cost"),
                                                  source_type="table")]) == []


def test_context_model_subject_must_precede_the_value_not_borrow_other_models_values():
    question = "从 GPT-1 到 GPT-3，最大上下文 Context Window 分别是多少？"
    evidence = [anchor("context size from 512 to 1024", "E1"), anchor("n_ctx 2048", "E2")]
    claims = [AnswerClaim(text="GPT-2 的上下文由512到1024 tokens", evidence_ids=["E1"]),
              AnswerClaim(text="GPT-3 的上下文为2048 tokens", evidence_ids=["E2"])]
    assert requested_models(question) == ["GPT-1", "GPT-2", "GPT-3"]
    assert missing_fields(question, claims, evidence) == ["GPT-1 最大上下文及来源说明"]
    claims.append(AnswerClaim(text="GPT-1 未给出窗口，GPT-2 为1024 tokens", evidence_ids=["E1"]))
    assert missing_fields(question, claims, evidence) == ["GPT-1 最大上下文及来源说明"]


def test_training_sequence_is_not_context_window_evidence():
    assert missing_fields("GPT-1 最大上下文是多少？", [
        AnswerClaim(text="GPT-1 最大上下文为512 tokens", evidence_ids=["E1"])
    ], [anchor("We train on sequences of 512 tokens")])


def test_parameter_sizes_without_model_mapping_are_incomplete():
    question = "GPT-1、GPT-2、GPT-3 参数量演化？"
    evidence = [anchor("parameters 117M 1542M 175B")]
    assert len(missing_fields(question, [AnswerClaim(
        text="模型规模117M、1542M、175B", evidence_ids=["E1"]
    )], evidence)) == 3
    claims = [AnswerClaim(text="GPT-1: 117M；GPT-2: 1542M；GPT-3: 175B", evidence_ids=["E1"])]
    assert missing_fields(question, claims, evidence) == []


def test_architecture_comparison_requires_both_relation_parts_and_two_sources():
    question = "BERT 承袭和丢弃 Transformer 哪些部分？"
    evidence = [anchor("original encoder decoder", "E1", "original"),
                anchor("bidirectional Transformer encoder", "E2", "bert")]
    claims = [AnswerClaim(text="BERT 基于原始编码器", evidence_ids=["E2"]),
              AnswerClaim(text="原模型采用encoder-decoder", evidence_ids=["E1"])]
    assert missing_fields(question, claims, evidence)
    joint = AnswerClaim(text="据两篇架构描述推断，BERT 沿用编码器，不使用原始解码器",
                        evidence_ids=["E1", "E2"])
    assert missing_fields(question, [joint], evidence) == []
    joint.evidence_ids = ["E2"]
    assert missing_fields(question, [joint], evidence)


def test_dialogue_history_does_not_create_requirements_for_the_current_question():
    question = "Prior dialogue:\nGPT-1 GPT-2 参数量\n\nCurrent user question:\n作者是谁？"
    assert current_question(question) == "作者是谁？"
    assert contract_prompt(question) == question


@pytest.mark.asyncio
async def test_agent_marks_partial_answer_without_calling_for_a_retry():
    class Registry:
        async def execute(self, *args):
            return [anchor("GPT-2 context size from 512 to 1024")]

    class Provider:
        name, model, calls = "test", "test", 0

        async def generate_grounded_answer(self, question, evidence):
            self.calls += 1
            assert "one explicit model-name-to-context-window claim" in question
            return GeneratedAnswer(answer="unchecked",
                                   claims=[GeneratedClaim(text="GPT-2 context is 1024 tokens",
                                                          evidence_ids=["E1"])])

    provider = Provider()
    result = await ResearchAgent(None, registry=Registry(), provider=provider).ask(
        AskRequest(question="GPT-1、GPT-2 上下文 Context Window 分别是多少？")
    )
    assert provider.calls == 1
    assert result.insufficient_evidence and result.claims
    assert "GPT-1" in result.answer and "部分结果" in result.answer
    assert result.trace[-1].status == "incomplete"


@pytest.mark.asyncio
async def test_agent_completes_bleu_from_actual_table_without_extra_generation():
    class Registry:
        async def execute(self, *args):
            return [anchor(TABLE, source_type="table")]

    class Provider:
        name, model, calls = "test", "test", 0

        async def generate_grounded_answer(self, *args):
            self.calls += 1
            return GeneratedAnswer(answer="incomplete", claims=[])

    provider = Provider()
    result = await ResearchAgent(None, registry=Registry(), provider=provider).ask(
        AskRequest(question=BLEU_QUESTION)
    )
    assert provider.calls == 1 and not result.insufficient_evidence
    assert "2.04" in result.answer and "推导" in result.answer
    assert result.trace[-1].status == "ok"
