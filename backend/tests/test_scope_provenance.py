import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import audit_grounded_claims


def anchor(key, quote, kind="text"):
    return EvidenceAnchor(
        evidence_id=key,
        document_id="original",
        page_number=1,
        quote=quote,
        source_type=kind,
        score=1,
    )


def audit(text, evidence):
    return audit_grounded_claims(
        [GeneratedClaim(text, [e.evidence_id for e in evidence])], evidence
    )


@pytest.mark.parametrize("summary,dialogue", [("0.25", "0.7"), ("0.42", "0.83")])
def test_sampling_values_stay_bound_to_their_actual_task_not_the_union(summary, dialogue):
    evidence = [
        anchor(
            "E1",
            f"Sample summaries from the TL;DR test set. "
            f"DPO sample generated with a temperature {summary}.",
        ),
        anchor(
            "E2",
            "Responses to a prompt from the Anthropic-HH test set. "
            f"DPO sample generated with temperature {dialogue}.",
        ),
    ]
    bad = f"对话实验报告了温度 {dialogue}、{summary} 的采样设置。"
    accepted, report = audit(bad, evidence)
    assert not accepted and "sampling_temperature_task_mismatch" in str(report)
    good = f"摘要实验使用温度 {summary}，对话实验使用温度 {dialogue}。"
    assert audit(good, evidence)[1].accepted_claim_count == 1
    assert audit(f"推理/评估中使用温度 {summary}、{dialogue}。", evidence)[0]


def test_task_in_an_unrelated_or_reference_excerpt_cannot_authorize_a_setting():
    evidence = [
        anchor("E1", "Sample summaries with temperature 0.42."),
        anchor("E2", "Dialogue sampling at temperature 0.42.", "reference"),
    ]
    assert not audit("对话实验采用温度0.42。", evidence)[0]
    assert audit(
        "对话实验采用温度0.42。",
        [evidence[0], anchor("E2", "We use temperature 0.42; task not specified.")],
    )[0]


@pytest.mark.parametrize("label", ["temp", "TEMP"])
def test_plot_temperature_alias_retains_task_before_a_later_mixed_caption(label):
    evidence = [
        anchor("E1", "For summarization, temperatures vary from 0.0 to 1.0."),
        anchor("E2", "Dialogue Win Rate Evolution\n"
               f"DPO ({label} = 0.83)\n"
               "Figure: Anthropic-HH dialogue improves over chosen summaries."),
    ]
    accepted, report = audit("摘要评估的采样温度为0.83。", evidence)
    assert not accepted and "sampling_temperature_task_mismatch" in str(report)
    assert audit("对话评估的采样温度为0.83。", evidence)[0]
    assert audit("实验与评估使用采样温度0.83。", evidence)[0]


def test_temperature_plot_alias_never_matches_an_unrelated_word_substring():
    from app.services.scope_provenance import sampling_task_conflicts

    evidence = [anchor("E1", "For dialogue, attempt = 0.83, no temperature setting reported.")]
    assert not sampling_task_conflicts("摘要评估的温度为0.83。", evidence)


def test_task_range_and_win_rates_are_not_interpreted_as_other_temperature_values():
    evidence = [
        anchor(
            "E1",
            "For summarization, we sample completions at temperatures "
            "varying from 0.0 to 1.0. The win rate is 61%.",
        )
    ]
    assert audit("摘要任务的温度范围为0.0到1.0。", evidence)[0]
    # No task assignment is fabricated for 0.5, or for the unrelated win rate.
    from app.services.scope_provenance import sampling_task_conflicts

    assert not sampling_task_conflicts("对话中温度为0.5。", evidence)
    assert not sampling_task_conflicts("对话中温度为61。", evidence)


def test_paper_scope_does_not_collapse_algorithm_and_experiments_to_only_analysis():
    evidence = [
        anchor(
            "E1",
            "We propose FastExample, an exact attention algorithm. "
            "We analyze its IO complexity. Experiments show training speedups.",
        )
    ]
    for text in (
        "FastExample限制在注意力算法自身的IO复杂度分析。",
        "The paper is limited to theoretical analysis.",
    ):
        accepted, report = audit(text, evidence)
        assert not accepted and "paper_not_limited_to_analysis" in str(report)
    assert audit("本次比较仅聚焦FastExample的IO复杂度分析。", evidence)[0]
    assert audit("FastExample的复杂度分析仅针对IO，另有训练实验。", evidence)[0]
    assert audit("该论文不仅做复杂度分析，还包含算法和训练实验。", evidence)[0]
    assert audit("FastExample仅做复杂度分析。", [anchor("E1", "We analyze IO complexity.")])[0]


def test_restricted_attention_compute_reduction_does_not_reverse_path_length_increase():
    evidence = [
        anchor(
            "E1",
            "Attention could be restricted to a neighborhood. "
            "This would increase the maximum path length.",
        )
    ]
    assert not audit("模型以限制邻域等方式降低长序列计算路径长度。", evidence)[0]
    assert audit("限制邻域可以降低计算量，同时增加最大路径长度。", evidence)[0]
