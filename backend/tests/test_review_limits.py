import json

import pytest

from app.llm.base import LLMResponseError
from app.llm.openai_responses import OpenAIResponsesProvider
from app.llm.review_limits import SHORT_REVIEW_PREFIX
from app.schemas.agent import EvidenceAnchor


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["short", "long", "too_many", "ordinary"])
async def test_only_explicit_review_role_is_bounded_and_raw_failure_preserved(mode):
    captured = []
    text = "A supported theme." if mode in {"short", "too_many"} else "x" * 161
    raw = {
        "answer": "",
        "claims": [{"text": text, "evidence_ids": ["R1"]}] * (9 if mode == "too_many" else 1),
    }
    provider = OpenAIResponsesProvider(
        api_key="test-placeholder", model="test", base_url="https://example.test", timeout_seconds=1
    )

    async def respond(payload):
        captured.append(payload)
        return {
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(raw)}]}
            ],
        }

    provider._post_response = respond
    question = "Explain the theme." if mode == "ordinary" else SHORT_REVIEW_PREFIX + "概述主题。"
    evidence = [
        EvidenceAnchor(
            evidence_id="R1", document_id="one", page_number=1, quote="A supported theme.", score=1
        )
    ]
    if mode in {"long", "too_many"}:
        with pytest.raises(LLMResponseError, match="未截断") as error:
            await provider.generate_grounded_answer(question, evidence)
        assert error.value.raw_grounded_output == raw
    else:
        result = await provider.generate_grounded_answer(question, evidence)
        assert result.claims[0].text == text
    assert len(captured) == 1
    claims_schema = captured[0]["text"]["format"]["schema"]["properties"]["claims"]
    assert ("maxItems" in claims_schema) is (mode != "ordinary")
    if mode != "ordinary":
        assert claims_schema["maxItems"] == 8
        assert claims_schema["items"]["properties"]["text"]["maxLength"] == 160


def test_scaling_span_keeps_some_trends_and_greater_than_qualifiers_without_answer_values():
    from app.llm.review_limits import unqualified_scaling_span

    evidence = [
        EvidenceAnchor(
            evidence_id="R1",
            document_id="one",
            page_number=1,
            score=1,
            quote="The loss scales with model size, "
            "with some trends spanning more than eleven orders of magnitude.",
        )
    ]
    assert unqualified_scaling_span("损失按幂律下降，跨越十一个数量级。", evidence)
    assert not unqualified_scaling_span("部分缩放趋势覆盖超过十一个数量级。", evidence)
    assert not unqualified_scaling_span("Cross-entropy loss scales with model size.", evidence)


def test_speed_comparison_preserves_relative_dimension_not_vague_shortness():
    from app.llm.review_limits import unqualified_dimension_condition

    evidence = [
        EvidenceAnchor(
            evidence_id="R1",
            document_id="one",
            page_number=1,
            score=1,
            quote="Layers are faster when the sequence length m is smaller "
            "than hidden dimensionality h.",
        )
    ]
    assert unqualified_dimension_condition("短序列时计算更快。", evidence)
    assert not unqualified_dimension_condition("序列长度小于隐藏维度时计算更快。", evidence)
    assert not unqualified_dimension_condition("For m < h these layers are faster.", evidence)
    assert not unqualified_dimension_condition("在m小于h时，自注意力更快。", evidence)
    assert not unqualified_dimension_condition(
        "When m less than h, these layers are faster.", evidence,
    )
    assert unqualified_dimension_condition("较短的m序列更快。", evidence)
    assert not unqualified_dimension_condition("Sequential depth is constant.", evidence)
    assert not unqualified_dimension_condition("Short inputs are faster.", [])


def test_warmup_motivation_cannot_become_an_unconditional_requirement():
    from app.llm.review_limits import unqualified_warmup_necessity

    source = [
        EvidenceAnchor(
            evidence_id="R1",
            document_id="one",
            page_number=1,
            score=1,
            quote="Using a large learning rate on those gradients makes the training unstable. "
            "Warm-up is practically helpful for avoiding this problem.",
        )
    ]
    assert unqualified_warmup_necessity("该配置需要学习率预热。", source)
    assert not unqualified_warmup_necessity("使用大学习率时，该配置需要预热。", source)
    assert not unqualified_warmup_necessity("该配置通常需要预热。", source)
    assert not unqualified_warmup_necessity("文中另一配置可以移除预热。", source)
    assert not unqualified_warmup_necessity("Some configurations need warm-up.", [])
    source[0].quote = "Therefore, us- ing a large learning rate makes the training unstable."
    assert unqualified_warmup_necessity("该配置需要学习率预热。", source)
    assert unqualified_warmup_necessity("该配置故需学习率warm-up。", source)
    assert not unqualified_warmup_necessity("另一配置无需学习率warm-up。", source)


def test_flop_comparison_cannot_borrow_only_a_memory_access_result():
    from app.llm.review_limits import ungrounded_compute_increase

    source = [
        EvidenceAnchor(
            evidence_id="R1",
            document_id="one",
            page_number=1,
            score=1,
            quote="Tiling reduces memory reads and writes.",
        )
    ]
    assert ungrounded_compute_increase("FLOP不降反升，但运行更快。", source)
    source[0].quote = "The method has higher FLOP count due to recomputation."
    assert not ungrounded_compute_increase("FLOP不降反升，但运行更快。", source)
    assert not ungrounded_compute_increase("Tiling reduces memory accesses.", source)
