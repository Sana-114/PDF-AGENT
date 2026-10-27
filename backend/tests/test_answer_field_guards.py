"""Adversarial field-completeness cases, not live LLM or scientific truth tests."""

import json

import httpx
import pytest

from app.agent.harness import ResearchAgent
from app.llm.openai_responses import OpenAIResponsesProvider
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.services.answer_completeness import (
    affirmative_model_values,
    contract_prompt,
    missing_fields,
)
from scripts.review_official_first_round import check_reviewed_fact


def evidence(quote, key="E1"):
    return EvidenceAnchor(evidence_id=key, document_id="paper", page_number=4,
                          quote=quote, score=0.8)


@pytest.mark.parametrize("text", [
    "GPT-1 的最大上下文窗口未被所提供证据明确给出；512只是后续论文比较。",
    "GPT-1 的上下文没有明确报告，不能据此确定为512 tokens。",
    "GPT-1 的上下文窗口无法从当前材料确认；512不是确定答案。",
    "GPT-1 的 context window is not explicitly stated; 512 appears in another comparison.",
    "GPT-1 context window cannot be determined from the 512-token comparison.",
    "GPT-1 context window might be 512 tokens, but this is speculation.",
    "GPT-1 的最大上下文可能是512 tokens，但这只是猜测。",
    "无法确定 GPT-1 的最大上下文是不是512 tokens。",
    "There is insufficient evidence for GPT-1 context window of 512 tokens.",
    "GPT-1 的上下文为512 tokens；但这只是猜测。",
])
def test_refusal_or_speculation_with_a_number_does_not_complete_the_field(text):
    assert missing_fields("GPT-1 最大上下文是多少？", [
        AnswerClaim(text=text, evidence_ids=["E1"])
    ], [evidence("We increase the context size from 512 to 1024 tokens.")])


@pytest.mark.parametrize("text", [
    "GPT-1 在2018年提出，其上下文相关信息见引用。",
    "GPT-1 采用2048维隐藏层，其上下文窗口另见论文。",
    "GPT-1 的 batch size 是512 tokens，上下文窗口没有具体说明。",
    "GPT-1 context window information is discussed in section 512.",
])
def test_year_dimension_batch_or_section_number_is_not_a_window_answer(text):
    assert missing_fields("GPT-1 最大上下文是多少？", [
        AnswerClaim(text=text, evidence_ids=["E1"])
    ], [evidence("2018 d_model 2048; batch size 512 tokens; context window 1024 tokens.")])


def test_window_value_cannot_borrow_the_same_number_from_hidden_dimension():
    assert missing_fields("GPT-3 最大上下文是多少？", [
        AnswerClaim(text="GPT-3 的最大上下文为2048 tokens", evidence_ids=["E1"])
    ], [evidence("d_model = 2048. All models use a context window of 4096 tokens.")])


def test_training_tokens_do_not_prove_parameter_count():
    assert missing_fields("GPT-1 参数量是多少？", [
        AnswerClaim(text="GPT-1 的参数量为300B", evidence_ids=["E1"])
    ], [evidence("All parameters are trained using 300B tokens.")])


@pytest.mark.parametrize("text", [
    "GPT-1 的上下文为512 tokens（根据后续论文的比较推断，非原论文直接报告）。",
    "GPT-1 原论文未直接给出窗口；根据后续配置比较推断，上下文为512 tokens。",
    "GPT-1: 512 tokens; this is inferred from the later paper, not directly reported originally.",
    "GPT-1 的上下文并非1024，而是512 tokens。",
    "GPT-1 原论文未直接给出窗口，但根据后续配置比较推断，上下文为512 tokens。",
])
def test_explicit_qualified_answers_are_not_confused_with_a_refusal(text):
    assert missing_fields("GPT-1 最大上下文是多少？", [
        AnswerClaim(text=text, evidence_ids=["E1"])
    ], [evidence("We increase the context size from 512 to 1024 tokens.")]) == []


def test_compound_question_must_cover_parameters_and_context_not_only_first_intent():
    question = "GPT-1 的参数量和最大上下文分别是多少？"
    source = [evidence("The model has 80M parameters; context window = 1536 tokens.")]
    count = AnswerClaim(text="GPT-1 参数量为80M", evidence_ids=["E1"])
    assert missing_fields(question, [count], source) == ["GPT-1 最大上下文及来源说明"]
    assert missing_fields(question, [count, AnswerClaim(
        text="GPT-1 的上下文为1536 tokens", evidence_ids=["E1"]
    )], source) == []
    prompt = contract_prompt(question)
    assert "parameter-count claim" in prompt and "context-window claim" in prompt


def test_strict_fact_review_does_not_count_a_named_refusal_with_the_expected_number():
    fact = {"source_filename": "later.pdf", "source_pages": [4],
            "source_text_contains_any": ["context size from 512 to 1024"],
            "claim_contains_any": ["512"], "claim_contains_any_groups": [["GPT-1"]]}
    response = type("Response", (), {
        "claims": [AnswerClaim(text="GPT-1 的窗口未被明确给出，512只是其他论文比较。",
                               evidence_ids=["E1"])],
        "evidence": [evidence("context size from 512 to 1024 tokens.")],
    })()
    assert not check_reviewed_fact(fact, response, {"later.pdf": "paper"})


def test_fact_review_binds_expected_value_not_a_different_affirmative_value_in_the_same_claim():
    fact = {"source_filename": "later.pdf", "source_pages": [4],
            "source_text_contains_any": ["context size from 512 to 1024"],
            "claim_contains_any": ["512"], "claim_contains_any_groups": [["GPT-1"]]}
    response = type("Response", (), {
        "claims": [AnswerClaim(text="GPT-1 的上下文为1024 tokens；512未被确认。",
                               evidence_ids=["E1"])],
        "evidence": [evidence("context size from 512 to 1024 tokens.")],
    })()
    assert not check_reviewed_fact(fact, response, {"later.pdf": "paper"})


@pytest.mark.asyncio
async def test_explicit_document_scope_is_enforced_before_generation_even_for_a_bad_tool_result():
    class Registry:
        async def execute(self, *args):
            return [evidence("An unrelated private-paper passage")]

    class Provider:
        name, model = "forbidden", "unused"

        async def generate_grounded_answer(self, *args):
            pytest.fail("A foreign-scope passage may not be sent to an LLM")

    response = await ResearchAgent(None, registry=Registry(), provider=Provider()).ask(
        AskRequest(question="所选论文的核心结论？", document_ids=["selected-paper"]))
    assert response.insufficient_evidence and not response.evidence


@pytest.mark.asyncio
async def test_generation_prompt_allows_partial_answers_without_contradictory_all_or_none_rule():
    def respond(request):
        prompt = json.loads(request.content)["instructions"]
        assert "If NONE of the requested parts is supported" in prompt
        assert "publish the supported claims" in prompt
        assert "insufficient for every" not in prompt
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{
            "type": "output_text", "text": json.dumps({"answer": "", "claims": []}),
        }]}]})

    provider = OpenAIResponsesProvider(api_key="local-test-placeholder", model="test",
        base_url="https://offline.invalid", timeout_seconds=1,
        transport=httpx.MockTransport(respond))
    generated = await provider.generate_grounded_answer("复合问题", [evidence("公开合成片段")])
    assert generated.claims == []


def test_chinese_window_size_increase_keeps_the_final_value_not_only_the_old_value():
    text = "GPT-2 的上下文大小从640增加到1280个token；这是后续论文自身的比较。"
    assert affirmative_model_values(text, "model_context") == {"GPT-2": {"1280"}}
    assert missing_fields("GPT-2 最大上下文？", [AnswerClaim(text=text, evidence_ids=["E1"])],
                          [evidence("We increase the context size from 640 to 1280 tokens.")]) == []


def test_parameter_range_and_repeated_family_variant_list_are_supported_without_gpt_gold():
    text = "GPT-4 参数量从80M到900M，最大模型称为GPT-4。"
    assert affirmative_model_values(text, "model_parameters")["GPT-4"] == {
        "quantity:80000000", "quantity:900000000"}
    text = "GPT-4 各规模模型参数量依次为：GPT-4 Small 80M、GPT-4 Medium 250M 和 GPT-4 900M。"
    assert "quantity:900000000" in affirmative_model_values(text, "model_parameters")["GPT-4"]
    assert missing_fields("GPT-4 参数量是多少？", [AnswerClaim(text=text, evidence_ids=["E1"])],
                          [evidence("Parameters Layers d_model\n80M 4 256; 900M 16 512")]) == []


def test_variant_list_does_not_relabel_a_training_token_count_even_with_a_parameter_label():
    text = "GPT-4 参数量另见表；GPT-4 900M tokens 是训练用量。"
    assert affirmative_model_values(text, "model_parameters") == {}


@pytest.mark.parametrize("text", [
    "无法确认 BERT 是否继承编码器和抛弃解码器。",
    "BERT 可能继承编码器且抛弃解码器，这只是猜测。",
    "BERT might inherit the encoder and discard the decoder.",
    "BERT 继承编码器，抛弃解码器；但这只是猜测。",
])
def test_two_citations_do_not_make_an_uncertain_architecture_relationship_complete(text):
    sources = [evidence("An encoder-decoder architecture", "E1"),
               evidence("Bidirectional Transformer encoder", "E2").model_copy(
                   update={"document_id": "second-paper"})]
    assert missing_fields("BERT 继承和丢弃哪些架构？", [
        AnswerClaim(text=text, evidence_ids=["E1", "E2"])
    ], sources) == ["承袭关系的双来源结论", "去除部分的双来源结论"]


def test_uncertain_removed_relation_does_not_erase_the_separately_supported_inheritance():
    sources = [evidence("An encoder-decoder architecture", "E1"),
               evidence("Bidirectional Transformer encoder", "E2").model_copy(
                   update={"document_id": "second-paper"})]
    claim = AnswerClaim(text="据两篇架构描述推断，BERT 沿用编码器；无法确认是否抛弃解码器。",
                        evidence_ids=["E1", "E2"])
    assert missing_fields("BERT 继承和丢弃哪些架构？", [claim], sources) == ["去除部分的双来源结论"]
