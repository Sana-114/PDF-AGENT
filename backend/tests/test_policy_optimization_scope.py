import pytest

from app.agent.harness import ResearchAgent
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.schemas.agent import AskRequest, EvidenceAnchor
from app.services.grounded_text import audit_grounded_claims, grounding_rejection_warnings

SOURCE = (
    "A reward model parameterization enables extraction of the corresponding optimal policy "
    "in closed form, allowing us to solve the standard RLHF problem with a simple "
    "classification loss, eliminating the need for sampling from the LM during fine-tuning."
)
OLD = ("DPO 用简单分类损失在闭式下求解 RLHF 的标准问题，省去采样与显著超参调优；"
       "InstructGPT 则先用示范监督微调再用人类偏好排序做强化学习，流程更复杂。")


def anchor(key="E1", quote=SOURCE, source="text", doc="paper"):
    return EvidenceAnchor(evidence_id=key, document_id=doc, page_number=1,
                          quote=quote, source_type=source, score=0.9)


def audit(text, evidence=None, ids=None):
    return audit_grounded_claims([GeneratedClaim(text, ids or ["E1"])], evidence or [anchor()])


def reasons(report):
    return " ".join(str(row.model_dump()) for row in report.rejections)


def test_old_real_ambiguous_claim_is_held_without_rewriting():
    accepted, report = audit(OLD)
    assert not accepted
    assert "closed_form_policy_object_omitted" in reasons(report)
    assert "lm_sampling_finetuning_scope_omitted" in reasons(report)
    warnings = grounding_rejection_warnings(report)
    assert len(warnings) == 1 and "策略对象" in warnings[0] and "微调阶段" in warnings[0]


@pytest.mark.parametrize("text", [
    "该方法用简单分类损失在闭式下求解标准优化问题。",
    "The method solves the classification loss in closed form.",
    "该方法能解析解整个模型的优化过程。",
])
def test_closed_form_keeps_the_actual_object(text):
    accepted, report = audit(text)
    assert not accepted and "closed_form_policy_object_omitted" in reasons(report)


def test_displayed_policy_solution_does_not_authorize_analytic_network_fit():
    source = anchor(quote="Hence we have the optimal solution: π(y|x) = π∗(y|x) = exp(r/β)/Z(x).")
    assert audit("最优策略有闭式表达。", [source])[0]
    assert not audit("用分类损失在闭式下求出整个模型。", [source])[0]


@pytest.mark.parametrize("text", [
    "最优策略有闭式表达，因此可以闭式直接训练模型。",
    "最优策略可闭式求解模型参数。",
    "The policy is analytical; network weights are obtained in closed form.",
])
def test_policy_word_elsewhere_cannot_authorize_analytic_model_fitting(text):
    accepted, report = audit(text)
    assert not accepted
    assert ("policy_expression_not_analytic_model_fit" in reasons(report)
            or "closed_form_policy_object_omitted" in reasons(report))


@pytest.mark.parametrize("text", [
    "最优策略有闭式表达，模型通过分类损失进行微调。",
    "The optimal policy has a closed-form expression; the model learns a classification loss.",
    "该方法微调时无需从语言模型采样，通过分类损失学习。",
    "No need for sampling from the LM during fine-tuning.",
    "最优策略有闭式表达，但这不意味着网络参数可以直接解析求出。",
    "文中微调方法免去从当前模型在线采样。",
    "该方法微调期间避免从语言模型在线采样。",
])
def test_qualified_claims_survive_unchanged(text):
    accepted, report = audit(text)
    assert report.accepted_claim_count == 1 and accepted[0].text == text


@pytest.mark.parametrize("text", [
    "免去的采样指在微调阶段从语言模型采样；该方法也免去细调期间从 LM 采样。",
    "该方法细调期间免去从语言模型采样。",
    "该方法精调阶段无需从 LM 采样。",
    "No need for sampling from the LM during finetuning.",
    "No need for sampling from the language model during fine tuning.",
])
def test_explicit_finetuning_aliases_do_not_erase_a_supported_scope(text):
    accepted, report = audit(text)
    assert report.accepted_claim_count == 1 and accepted[0].text == text


@pytest.mark.parametrize("text", [
    "细调期间无需从 LM 采样；整个流程无需从 LM 采样。",
    "精调阶段无需采样。",
    "No need for sampling from the LM throughout training.",
])
def test_finetuning_alias_does_not_authorize_an_unbounded_or_objectless_claim(text):
    assert not audit(text)[0]


def test_source_spelling_alias_does_not_disable_the_scope_guard():
    source = anchor(quote=SOURCE.replace("fine-tuning", "finetuning"))
    assert not audit("该方法无需从语言模型采样。", [source])[0]


@pytest.mark.parametrize("text", [
    "该方法省去采样与显著超参调优。",
    "该方法无需从语言模型采样。",
    "该方法在微调时无需采样。",
    "It eliminates sampling from the LM.",
    "It does not require sampling.",
    "微调模型不需要任何采样。",
    "该方法以分类损失避开RLHF采样。",
    "该方法避免从语言模型采样。",
    "该方法不再进行采样。",
])
def test_sampling_elimination_retains_stage_and_sampled_object(text):
    accepted, report = audit(text)
    assert not accepted and "lm_sampling_finetuning_scope_omitted" in reasons(report)


def test_bibliography_unrelated_or_uncited_paper_does_not_trigger_rule():
    plain = anchor(quote="An unrelated optimizer has an analytical model fit.")
    extra = anchor("E2", doc="other")
    assert audit("模型拟合具有闭式解。", [plain, extra])[1].accepted_claim_count == 1
    assert audit("模型拟合具有闭式解。", [anchor(source="reference")])[1].accepted_claim_count == 1


@pytest.mark.parametrize("text", [
    "该方法因而省去显式奖励建模与 RL 采样。",
    "该方法微调期间无需从语言模型采样。",
    "The method eliminates sampling from the LM during fine-tuning.",
])
def test_sampling_exemption_cannot_bypass_support_by_omitting_the_restricting_excerpt(text):
    unrelated = anchor(quote="We directly optimize preferences with a classification objective.")
    accepted, report = audit(text, [unrelated, anchor("E2")], ["E1"])
    assert not accepted
    assert "sampling_avoidance_missing_direct_cited_support" in reasons(report)
    assert audit(text)[1].accepted_claim_count == (0 if "RL 采样" in text else 1)


def test_each_original_must_support_an_asserted_shared_constraint():
    first = anchor(quote="We use a KL-divergence constraint.")
    other = anchor("E2", quote="We train a reward model and optimize a policy.", doc="other")
    for text in ("两篇原文均使用带 KL 约束的目标。", "Both methods use a KL constraint."):
        accepted, report = audit(text, [first, other], ["E1", "E2"])
        assert not accepted and "shared_constraint_missing_from_each_original" in reasons(report)
        supported = other.model_copy(update={"quote": "Our policy has a KL penalty."})
        assert audit(text, [first, supported], ["E1", "E2"])[1].accepted_claim_count == 1
    assert audit("第一篇含 KL 约束；第二篇训练奖励模型。", [first, other], ["E1", "E2"])[0]
    assert audit("两篇并非都使用 KL 约束。", [first, other], ["E1", "E2"])[0]
    assert audit("Not both methods use a KL constraint.", [first, other], ["E1", "E2"])[0]


def test_a_policy_in_another_proposition_does_not_cover_the_missing_object():
    accepted, report = audit("最优策略可明确表述；用分类损失在闭式下求解模型。")
    assert not accepted and "closed_form_policy_object_omitted" in reasons(report)


def test_denial_of_analytic_parameter_fit_is_not_a_positive_closed_form_claim():
    text = ("闭式的对象是对应最优策略，而非神经网络参数；论文用分类损失学习，"
            "因此并不存在对模型参数的闭式解。")
    assert audit(text)[1].accepted_claim_count == 1
    assert not audit(text + " 但可以闭式求解模型参数。")[0]


@pytest.mark.parametrize("subject", ["这", "该结果", "这一结果", "上述结果"])
def test_source_report_denial_with_demonstrative_subject_survives_unchanged(subject):
    text = ("该方法可闭式提取相应的最优策略；"
            f"{subject}并未说明神经网络参数 θ 可一次闭式求出。")
    accepted, report = audit(text)
    assert report.accepted_claim_count == 1 and accepted[0].text == text


@pytest.mark.parametrize("denial", ["这不是说", "这不是在说"])
def test_explicit_not_saying_parameter_solution_is_a_denial_not_a_fit(denial):
    text = f"最优策略可闭式提取；{denial}网络参数能一次闭式求出。"
    assert audit(text)[1].accepted_claim_count == 1
    assert not audit(text + "但可以闭式求解模型参数。")[0]


def test_explicit_original_did_not_say_is_not_an_affirmative_parameter_fit():
    text = "最优策略可闭式提取；原文并未说神经网络参数能一次闭式求出。"
    assert audit(text)[1].accepted_claim_count == 1
    assert not audit(text + "但可以闭式求解模型参数。")[0]


@pytest.mark.parametrize("voice", ["这并未被描述为", "该结果未被定义成"])
def test_passive_reporting_denial_does_not_become_a_parameter_fit(voice):
    text = f"最优策略可闭式提取；{voice}对网络参数的一次闭式求解。"
    assert audit(text)[1].accepted_claim_count == 1
    assert not audit(text + "但可以闭式求解模型参数。")[0]


@pytest.mark.parametrize("subject", ["它", "其"])
def test_pronominal_reporting_denial_preserves_sampling_bound_without_hiding_errors(subject):
    text = ("该方法消去的仅是微调期间从 LM 采样这一环节，论文称其消除了 fine-tuning 时对 "
            f"LM 采样的需要；{subject}并未声称数据准备和推理整个流程都完全不需要采样。")
    accepted, report = audit(text)
    assert report.accepted_claim_count == 1 and accepted[0].text == text
    for continuation in ("但可以闭式求解模型参数。", "实际上整个流程完全无需任何采样。"):
        assert not audit(text + continuation)[0]


@pytest.mark.parametrize("text", [
    "可闭式求解整个模型；这并未说明网络权重如何存储。",
    "这并未说明网络参数的闭式解，但实际上能闭式求解模型参数。",
    "这并未说明最优策略，但网络参数可以闭式求解。",
])
def test_demonstrative_denial_cannot_hide_another_positive_analytic_fit(text):
    assert not audit(text)[0]


def test_quoted_sampling_restatement_inherits_only_explicit_finetuning_bound():
    scoped = ("免采样边界仅指微调时无需从语言模型采样；"
              "同一摘要仍把它描述为消除「从 LM 采样」的需求，"
              "并未声称数据准备或推理也不需要采样。")
    assert audit(scoped)[1].accepted_claim_count == 1
    assert not audit("同一摘要仍把它描述为消除「从 LM 采样」的需求。")[0]
    assert not audit(scoped.replace("消除「", "在整个算法中消除「"))[0]


def test_quoted_rejected_propositions_in_a_pure_source_note_are_not_positive_claims():
    note = ("所给证据未支持关于“推理阶段完全不需要任何采样”或“网络参数能一次闭式求出”"
            "的任何说法，也未报告数据准备阶段完全无采样的实现细节。")
    assert audit(note)[1].accepted_claim_count == 1
    assert not audit(note + "；可以闭式训练模型。")[0]
    assert not audit(note.rstrip("。") + "，但可以闭式训练模型。")[0]


@pytest.mark.parametrize("text", [
    "最优策略可闭式提取；这不是对网络权重或分类损失的闭式拟合。",
    "微调时不需要从语言模型采样；并未声称数据准备或推理完全不需要任何采样。",
    "闭式结果对应最优策略；它并不是闭式求出神经网络权重。",
    "闭式结果对应最优策略；它并不表示网络参数能一次性闭式求出。",
    "闭式结果对应最优策略；这并不表明可闭式求解网络参数。",
    "最优策略可闭式提取；论文并未声称网络参数本身能一次闭式求出。",
    "微调时无需从LM采样。这并不等同于整个流程完全无需任何采样。",
])
def test_explicit_negative_continuation_does_not_erase_a_correct_scoped_claim(text):
    assert audit(text)[1].accepted_claim_count == 1
    assert not audit(text + "；可闭式求解模型参数。")[0]


def test_negation_does_not_hide_a_positive_alternative_analytic_fit():
    assert not audit("并不是只提取最优策略，而是可闭式求解模型参数。")[0]
    assert not audit("并不表示只有策略闭式可解，而是可闭式求解模型参数。")[0]
    assert not audit("论文并未声称只有策略闭式可解，而是可闭式求解模型参数。")[0]


@pytest.mark.parametrize("text", [
    "它免去了微调阶段从语言模型采样；原文并未称数据准备、模型微调和推理的整个流程完全不需要任何采样。",
    "原文未报告“神经网络参数可一次闭式求出”或“整个流程完全免采样”的实现细节，因此这些说法超出了原文支持的范围。",
    "原文未报告整个流程无需采样。",
    "闭式结果对应最优策略；原文并未说明神经网络参数本身可由闭式解一次求出。",
])
def test_source_limitation_is_not_a_positive_sampling_or_parameter_assertion(text):
    assert audit(text)[1].accepted_claim_count == 1
    for continuation in (
        "；可以闭式求解模型参数。", "，但可以闭式求解模型参数。",
        "，实际上可以闭式求解模型参数。", "，并且可以闭式求解模型参数。",
        "，却可以闭式求解模型参数。",
    ):
        assert not audit(text.rstrip("。") + continuation)[0]


def test_contrast_propositions_preserve_correct_negation_without_hiding_positive_errors():
    assert audit("可闭式提取最优策略，而不是闭式求解神经网络参数。")[1].accepted_claim_count == 1
    assert not audit("可闭式求解模型参数，而非只提取最优策略。")[0]
    assert not audit("可闭式求解模型参数，并非只提取最优策略。")[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_only", [False, True])
async def test_general_comparison_uses_selected_papers_not_just_numeric_contracts(history_only):
    class Registry:
        calls = []

        async def execute(self, name, args, context):
            self.calls.append(args)
            return [anchor(quote="Original method description.", doc=args["document_ids"][0])]

    class Provider:
        name, model, calls = "mock", "test", 0

        async def generate_grounded_answer(self, question, evidence):
            self.calls += 1
            return GeneratedAnswer(answer="", claims=[GeneratedClaim(
                "Methods have distinct descriptions.", [e.evidence_id for e in evidence])])

    registry, provider = Registry(), Provider()
    question = "比较两篇论文的偏好学习流程。"
    if history_only:
        question += "\n\nCurrent user question:\n描述所选方法。"
    result = await ResearchAgent(None, registry=registry, provider=provider).ask(
        AskRequest(question=question, document_ids=["one", "two"], top_k=6))
    assert provider.calls == 1
    expected = [["one", "two"]] if history_only else [["one"], ["two"]]
    assert [row["document_ids"] for row in registry.calls] == expected
    assert {e.document_id for e in result.evidence} == ({"one"} if history_only else {"one", "two"})
