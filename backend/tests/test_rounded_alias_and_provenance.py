import pytest

from app.schemas.agent import EvidenceAnchor
from app.services.answer_completeness import affirmative_model_values
from app.services.grounded_text import unsupported_numeric_facts


def anchor(quote):
    return EvidenceAnchor(evidence_id="E1", document_id="doc", page_number=4, quote=quote, score=1)


@pytest.mark.parametrize("text", ["1542M（约 1.5B）", "1542M (about 1.54B)",
                                 "1542M（约15.4亿）", "1592M（约1.6B）"])
def test_close_labelled_alias_rounding_is_computed_from_cited_exact_number(text):
    assert not unsupported_numeric_facts(text, [anchor("Parameters 1542M 1592M")])


@pytest.mark.parametrize("text", ["1542M（约1.6B）", "1542M（约2B）", "1542M（约1.0B）",
                                 "1542M（1.5B）", "约1.5B", "1542M（约1.5M）"])
def test_wrong_unlabelled_standalone_or_materially_different_approximation_rejected(text):
    assert unsupported_numeric_facts(text, [anchor("Parameters 1542M")])


def test_exact_number_must_itself_be_present_in_a_cited_passage():
    assert unsupported_numeric_facts("1542M（约1.5B）", [anchor("Parameters 762M")])


def test_parenthetical_later_paper_does_not_steal_current_context_assignment():
    text = ("GPT-1（Radford et al., 2018，即 GPT-2 论文所述基线 OpenAI GPT 模型）的上下文窗口"
            "为 512 token：这是推断而非 GPT-1 原文的直接陈述，依据是 GPT-2 论文的配置比较。")
    assert "512" in affirmative_model_values(text, "model_context")["GPT-1"]


def test_explicit_model_size_synonym_can_cover_a_qualified_parameter_inference():
    text = ("GPT-1 论文本身没有直接报告其参数量；GPT-2 说明其最小模型等价于原始 GPT，"
            "表中为 117M，据此可推断 GPT-1 的规模约为 117M（不是 GPT-1 论文直接报告的数字）。")
    assert "quantity:117000000" in affirmative_model_values(text, "model_parameters")["GPT-1"]


def test_training_size_is_not_parameter_count_and_values_in_plain_lists_still_parse():
    assert not affirmative_model_values("GPT-1 的训练数据规模为117M tokens。", "model_parameters")
    assert "1024" in affirmative_model_values("GPT-2：1024。", "model_context")["GPT-2"]
