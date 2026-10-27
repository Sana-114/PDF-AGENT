import pytest

from app.services.answer_completeness import affirmative_model_values, contract_prompt
from app.services.grounded_text import normalise_fact


@pytest.mark.parametrize("text", [
    "GPT-1：原论文证据未直接给出参数量；但 GPT-2 的表及等价说明可据此推断"
    "GPT-1 对应 70M（此为后一论文的推断，并非 GPT-1 论文自身直接报告的数值）。",
    "From the parameter count table we infer GPT-1 corresponds to 70M.",
])
def test_explicit_parameter_inference_covers_the_named_subject(text):
    assert affirmative_model_values(text, "model_parameters")["GPT-1"] == {
        normalise_fact("70M")}


@pytest.mark.parametrize("text", [
    "GPT-1 对应 70M。",
    "参数量表另见正文，GPT-1 对应 70M 训练 tokens。",
    "参数量表另见正文，GPT-1 对应 70M FLOPs。",
    "参数量表另见正文，GPT-1 对应 70M 上下文词元。",
    "参数量表另见正文，也许 GPT-1 对应 70M。",
    "参数量表另见正文，GPT-1 对应 70M，但参数量未知。",
])
def test_missing_field_label_nonparameter_or_retracted_correspondence(text):
    assert "GPT-1" not in affirmative_model_values(text, "model_parameters")


def test_correspondence_does_not_cover_another_named_model():
    values = affirmative_model_values(
        "参数量：GPT-2 对应 450M，但 GPT-1 参数量未知。", "model_parameters")
    assert values == {"GPT-2": {normalise_fact("450M")}}


def test_context_contract_does_not_invent_a_training_phase_or_window():
    prompt = contract_prompt("比较 GPT-1 和 GPT-2 的上下文窗口")
    assert "omit unrequested training procedures and phase labels" in prompt
    assert "architectural baseline comparison" in prompt
    assert "512" not in prompt and "1024" not in prompt
