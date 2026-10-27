import pytest

from app.schemas.agent import AnswerClaim
from app.services.answer_completeness import missing_fields


@pytest.mark.parametrize(
    "question,refusal",
    [
        ("随机种子的具体值是多少？", "所给证据中没有任何一处报告随机种子。"),
        ("random seed value?", "The seed is not reported in the provided evidence."),
        ("随机种子的具体值是多少？", "所给证据中未出现随机种子的具体整数值。"),
        ("random seed value?", "The random seed was not found in the excerpts."),
        ("random_state 的整数编号是多少？", "random_state is not provided."),
    ],
)
def test_other_true_settings_cannot_cover_explicitly_unreported_seed(question, refusal):
    claims = [
        AnswerClaim(text=refusal, evidence_ids=["E1"]),
        AnswerClaim(text="训练执行 73 个步骤。", evidence_ids=["E1"]),
    ]
    assert any("随机种子" in field for field in missing_fields(question, claims, []))


@pytest.mark.parametrize(
    "question,text",
    [
        ("训练执行多少步？", "随机种子未报告，训练执行 73 步。"),
        ("随机种子的具体值是多少？", "随机种子设置为 17。"),
        ("以前：random seed value?\n\nCurrent user question:\n训练执行多少步？", "种子未报告。"),
    ],
)
def test_other_question_or_explicit_seed_assignment_is_not_mislabeled(question, text):
    assert not missing_fields(question, [AnswerClaim(text=text, evidence_ids=["E1"])], [])
