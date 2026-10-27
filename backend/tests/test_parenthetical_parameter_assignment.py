import pytest

from app.services.answer_completeness import affirmative_model_values
from app.services.grounded_text import normalise_fact


@pytest.mark.parametrize("text", [
    "GPT-3，为0.6亿（60M）参数。",
    "GPT-3 is 60M (60 million) parameters.",
    "GPT-3，是60M（0.6亿）个参数。",
    "GPT-3 论文使用与 GPT-2 相同的架构，其中最大的模型称为 GPT-3，"
    "为0.6亿（60M）参数。",
])
def test_equivalent_parenthetical_parameter_assignment(text):
    assert normalise_fact("60M") in affirmative_model_values(text, "model_parameters")["GPT-3"]


@pytest.mark.parametrize("text", [
    "GPT-3，为0.6亿（80M）参数。",
    "GPT-3，为0.6亿（60M）训练 tokens。",
    "GPT-3，为0.6亿（60M）FLOPs。",
    "GPT-3 的隐藏维度为0.6亿（60M）。",
    "GPT-3，也许为0.6亿（60M）参数。",
    "GPT-3，为0.6亿（60M）参数，但参数量未知。",
])
def test_unavailable_or_nonparameter_count_does_not_cover_model(text):
    assert "GPT-3" not in affirmative_model_values(text, "model_parameters")
