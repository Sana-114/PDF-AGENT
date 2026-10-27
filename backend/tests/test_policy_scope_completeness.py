import pytest

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.answer_completeness import missing_fields

QUESTION = ("闭式结果具体对应什么对象，免去的采样是什么对象、哪个阶段？"
            "请解释边界，不补充未报告的细节。")
SOURCE = ("The corresponding optimal policy in closed form, with a classification loss, "
          "eliminating the need for sampling from the LM during fine-tuning.")
EVIDENCE = [EvidenceAnchor(evidence_id="E1", document_id="one", page_number=1,
                           quote=SOURCE, source_type="text", score=1)]


def claim(text):
    return AnswerClaim(text=text, evidence_ids=["E1"])


@pytest.mark.parametrize("claims", [
    [claim("最优策略有闭式表达，通过分类损失学习。")],
    [claim("微调时无需从语言模型采样。")],
    [claim("原文未报告整个流程无需采样，也未报告模型参数可闭式求出。")],
])
def test_explicit_scope_fields_cannot_disappear_after_publication(claims):
    assert missing_fields(QUESTION, claims, EVIDENCE)


def test_direct_cited_scope_fields_are_complete_without_unrequested_details():
    claims = [claim("最优策略有闭式表达，通过分类损失学习。"),
              claim("微调时无需从语言模型采样。")]
    assert not missing_fields(QUESTION, claims, EVIDENCE)
    assert not missing_fields("介绍本文的研究方向。", claims[:1], EVIDENCE)


def test_positive_object_and_negative_parameter_contrast_share_one_claim():
    claims = [claim("闭式结果对应最优策略，并非神经网络参数一次闭式求出；通过分类损失学习。"),
              claim("免采样仅指微调期间不需要从语言模型采样，但数据准备仍有采样。")]
    assert not missing_fields(QUESTION, claims, EVIDENCE)


def test_uncited_restriction_and_denial_do_not_cover_the_sampling_field():
    claims = [claim("最优策略有闭式表达。"),
              claim("原文并未称微调期间无需从语言模型采样。")]
    assert missing_fields(QUESTION, claims, EVIDENCE)
    unsupported = [EVIDENCE[0].model_copy(update={"quote": "An unrelated abstract."})]
    assert missing_fields(QUESTION, [claim("最优策略有闭式表达；微调无需从LM采样。")], unsupported)


@pytest.mark.parametrize("text", [
    "细调期间免去从语言模型采样。",
    "精调阶段无需从 LM 采样。",
    "No need for sampling from the LM during finetuning.",
])
def test_completeness_accepts_the_same_explicit_stage_aliases_without_inventing_support(text):
    claims = [claim("最优策略有闭式表达。"), claim(text)]
    assert not missing_fields(QUESTION, claims, EVIDENCE)
    unrelated = [EVIDENCE[0].model_copy(update={"quote": "An unrelated method description."})]
    assert missing_fields(QUESTION, claims, unrelated)


@pytest.mark.parametrize("text", [
    "免采样有明确边界：消除的是微调期间从语言模型采样，并未声称整个流程完全不采样。",
    "免去的是微调期间从 LM 采样，这并未声称数据准备也不采样。",
    "未声称整个流程不采样；微调期间无需从语言模型采样。",
])
def test_contrastive_denial_does_not_erase_the_supported_positive_scope(text):
    claims = [claim("最优策略有闭式表达。"), claim(text)]
    assert not missing_fields(QUESTION, claims, EVIDENCE)


@pytest.mark.parametrize("text", [
    "无需采样，并未声称微调期间无需从语言模型采样。",
    "微调期间无需采样，但原文未报告从语言模型采样的边界。",
    "原文并未声称微调期间无需从语言模型采样。",
    "微调期间未声称免采样，并未声称免去从语言模型采样。",
])
def test_contrastive_denial_cannot_supply_missing_affirmative_scope_fields(text):
    assert missing_fields(QUESTION, [claim("最优策略有闭式表达。"), claim(text)], EVIDENCE)


def test_explicit_displayed_policy_solution_is_direct_source_not_an_implicit_citation():
    formula = EvidenceAnchor(evidence_id="E2", document_id="one", page_number=2, score=1,
        quote="Hence we have the optimal solution: π(y|x) = π∗(y|x) = πref(y|x) exp(r/β)/Z(x).")
    claims = [AnswerClaim(text="最优策略有闭式表达。", evidence_ids=["E2"]),
              claim("微调期间无需从语言模型采样。")]
    assert not missing_fields(QUESTION, claims, [*EVIDENCE, formula])
    # Available but uncited equations do not cover the field.
    assert missing_fields(QUESTION, claims, EVIDENCE)


@pytest.mark.parametrize("quote", [
    "We describe an optimal solution: minimizing a loss involving the policy π and exp(r/β).",
    "We derive the policy gradient ∇θ and use exp(r/β).",
    "The policy is π(y|x) = exp(r/β)/Z(x); no optimality result is stated here.",
])
def test_policy_or_exponential_mentions_alone_do_not_replace_a_direct_solution(quote):
    source = EVIDENCE[0].model_copy(update={"quote": quote})
    assert missing_fields(QUESTION, [claim("最优策略有闭式表达。")], [source])
