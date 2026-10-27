from app.llm.review_limits import filter_review_publication
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.claim_semantics import evidence_scope_note


def test_consolidated_guards_preserve_order_counts_and_published_claim_identity():
    source = EvidenceAnchor(
        evidence_id="R1", document_id="one", page_number=1, score=1,
        quote="Some trends spanning more than eleven orders of magnitude. "
        "Faster when the sequence length is smaller than hidden dimensionality. "
        "Using a large learning rate makes the training unstable. Tiling reduces IO.",
    )
    texts = [
        "本次证据不足，但这里仍需要预热。",
        "损失跨越十一个数量级。",
        "短序列更快。",
        "必须预热。",
        "FLOP增加。",
        "仅在大学习率条件下需要预热。",
    ]
    claims = [AnswerClaim(text=text, evidence_ids=["R1"]) for text in texts]
    kept, warnings = filter_review_publication(claims, [source])
    assert kept == [claims[-1]]
    assert kept[0] is claims[-1]
    assert len(warnings) == 5
    assert all("已暂缓 1 条" in warning for warning in warnings)
    assert [keyword in warning for keyword, warning in zip(
        ["说明", "数量级", "维度", "预热", "计算量"], warnings, strict=True,
    )] == [True] * 5
    # Both shortage and necessity match the first row; it is counted only once.
    assert sum("已暂缓 2 条" in warning for warning in warnings) == 0


def test_valid_claims_and_empty_results_do_not_gain_new_warnings_or_mutate_input():
    source = EvidenceAnchor(
        evidence_id="R1", document_id="one", page_number=1, score=1, quote="Mechanism.",
    )
    claims = [AnswerClaim(text="Mechanism.", evidence_ids=["R1"])]
    original = claims[0].model_dump()
    kept, warnings = filter_review_publication(claims, [source])
    assert kept == claims and warnings == []
    assert claims[0].model_dump() == original
    assert filter_review_publication([], []) == ([], [])


def test_input_scope_warning_is_not_a_sourced_literature_absence_fact():
    assert evidence_scope_note("The supplied excerpts do not report applying this combination.")
    assert evidence_scope_note("None of the supplied evidence reports this combination.")
    assert evidence_scope_note("所给摘录不足，无法判断这是否属于研究空白。")
    assert not evidence_scope_note("The cited experiment does not report the learning rate.")
    assert not evidence_scope_note("The supplied evidence describes the training mechanism.")


def test_reversed_source_word_order_still_preserves_large_learning_rate_condition():
    source = EvidenceAnchor(
        evidence_id="R1", document_id="one", page_number=8, score=1,
        quote="This leads to an unstable training when using a large learning rate.",
    )
    bad = AnswerClaim(text="初始化时输出层附近期望梯度大、需warm-up。", evidence_ids=["R1"])
    good = AnswerClaim(text="在大学习率条件下，预热有助于避免初始化时的训练不稳定。",
                       evidence_ids=["R1"])
    kept, warnings = filter_review_publication([bad, good], [source])
    assert kept == [good] and any("预热" in w for w in warnings)


def test_combination_absence_requires_explicit_original_not_an_excerpt_gap():
    source = EvidenceAnchor(
        evidence_id="R1", document_id="one", page_number=1, score=1,
        quote="We backpropagate through a quantized model into trainable adapters.",
    )
    claim = AnswerClaim(text="两者机制不同，原文未验证组合。", evidence_ids=["R1"])
    assert not filter_review_publication([claim], [source])[0]
    explicit = source.model_copy(update={
        "quote": "We did not evaluate the combination with retrieval.",
    })
    assert filter_review_publication([claim], [explicit])[0] == [claim]
