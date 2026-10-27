"""An explicit service role controls format only, never scientific answer values."""

import re

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.claim_semantics import evidence_scope_note as mixed_source_shortage_note

SHORT_REVIEW_PREFIX = "任务：简短领域综述。\n"
THEMATIC_REVIEW_PREFIX = "任务：主题化跨文综述。\n"
MAX_REVIEW_CLAIMS = 8
MAX_REVIEW_CLAIM_CHARS = 160


def opaque_paper_attribution(text):
    """Paper identity labels are metadata, not readable method names in prose."""
    return bool(re.search(r"(?<![A-Za-z0-9_])P\d+\s*(?:的|指出|提出|证明|用|以|则|采用|通过|显示)",
                          text))


def unqualified_scaling_span(text, cited):
    """Hold the narrow loss-vs-scaling-span ambiguity; not a general entailment check."""
    if not re.search(r"数量级|orders? of magnitude", text, re.I):
        return False
    scoped_source = any(
        re.search(r"some trends spanning more than", item.quote, re.I) for item in cited
    )
    if not scoped_source:
        return False
    return not all(
        re.search(pattern, text, re.I)
        for pattern in (r"趋势|trends?", r"部分|some", r"超过|以上|more than|over\s+\w+\s+orders?")
    )


def unqualified_dimension_condition(text, cited):
    """Hold a narrow faster-than claim which omits its length/dimension condition."""
    if not re.search(r"faster|更快|快于", text, re.I):
        return False
    scoped = any(
        re.search(
            r"when the sequence length\b.{0,100}smaller than\b.{0,100}dimensionality\b",
            " ".join(item.quote.split()),
            re.I,
        )
        for item in cited
    )
    if not scoped:
        return False
    natural_condition = re.search(r"维度|dimensions?|dimensionality", text, re.I) and re.search(
        r"小于|smaller|less|<", text, re.I
    )
    symbolic_condition = re.search(
        r"(?<![A-Za-z0-9_])[a-z]\s*(?:<|小于|less\s+than)\s*[a-z](?![A-Za-z0-9_])",
        text, re.I,
    )
    return not (natural_condition or symbolic_condition)


def unqualified_warmup_necessity(text, cited):
    """Do not turn a large-learning-rate stability motivation into an absolute need."""
    if not re.search(r"预热|warm[\s-]?up", text, re.I) or not re.search(
        r"需要|(?<!无)(?<!不)需(?:学习率|\s*warm[\s-]?up|预热)|必须|必不可少|"
        r"\b(?:requires?|needs?|must|essential)\b",
        text,
        re.I,
    ):
        return False
    scoped = any(
        re.search(
            r"\ba large learning rate\b.{0,100}training unstable|"
            r"unstable training.{0,80}\ba large learning rate\b",
            " ".join(item.quote.split()),
            re.I,
        )
        for item in cited
    )
    if not scoped:
        return False
    return not re.search(
        r"(?:大|较大|高)学习率|large learning rate|通常|往往|有助于|可能|usually|helpful|may",
        text,
        re.I,
    )


def unsupported_combination_absence(text, cited):
    """A paper-wide absence claim needs an explicit original limitation.

    Missing a sentence in a bounded excerpt does not prove the original study
    never evaluated a combination. This checks a narrow negative-claim form;
    it is not a general combination or literature completeness verifier.
    """
    absence = r"(?:原文|论文|研究).{0,12}(?:未|没有).{0,8}(?:验证|评估|测试).{0,8}(?:组合|联合)"
    if not re.search(absence, text):
        return False
    return not any(re.search(
        absence + r"|(?:did not|have not|not yet).{0,30}(?:evaluat|test|validat)"
        r".{0,35}(?:combin|joint)", " ".join(item.quote.split()), re.I,
    ) for item in cited if item.source_type != "reference")
def ungrounded_compute_increase(text, cited):
    """A compute increase needs its own original support, not an unrelated IO result."""
    if not re.search(r"FLOPs?|浮点(?:计算|操作)", text, re.I) or not re.search(
        r"不降反升|(?:更|较)高|增加|higher|more|increase", text, re.I
    ):
        return False
    return not any(
        re.search(
            r"higher FLOP|more FLOP|FLOP count.{0,50}(?:higher|increase)|recomputation",
            " ".join(item.quote.split()),
            re.I,
        )
        for item in cited
    )


def ungrounded_compute_nonreduction(text, cited):
    """An IO improvement alone proves neither unchanged nor increased FLOPs."""
    if not re.search(r"FLOPs?|浮点(?:计算|操作)", text, re.I) or not re.search(
        r"(?:不是|而非|并非|不|未)(?:在于|通过|靠)?(?:降低|减少)|"
        r"(?:does?\s+not|without|rather\s+than)\s+(?:reduc\w*|lower\w*)", text, re.I,
    ):
        return False
    return not any(item.source_type != "reference" and re.search(
        r"(?:higher|more|same|unchanged)\s+(?:number\s+of\s+)?FLOPs?|"
        r"FLOP\s+count.{0,50}(?:higher|increase|unchanged)|"
        r"(?:does?\s+not|without)\s+reduc\w*.{0,35}FLOPs?",
        " ".join(item.quote.split()), re.I,
    ) for item in cited)


def unqualified_compute_comparator(text, cited):
    """A cited FLOP result must retain its named baseline and backward-pass setting."""
    if not re.search(r"FLOPs?|浮点(?:计算|操作)", text, re.I) or not re.search(
        r"(?:更|较)高|增加|higher|more|increase", text, re.I,
    ):
        return False
    scoped = any(item.source_type != "reference" and re.search(
        r"higher FLOP count compared to standard attention.{0,90}backward pass",
        " ".join(item.quote.split()), re.I,
    ) for item in cited)
    return scoped and not (
        re.search(r"标准注意力|standard attention", text, re.I)
        and re.search(r"反向传播|反向过程|backward pass|backpropagation", text, re.I)
    )


def unqualified_gradient_stability(text, cited):
    """Retain explicit initialization scope for a source's well-behaved gradient result."""
    if not any(item.source_type != "reference" and re.search(
        r"gradients?\s+(?:are\s+)?well[-\s]behaved\s+at\s+initialization",
        " ".join(item.quote.split()), re.I,
    ) for item in cited):
        return False
    for clause in re.split(r"[。;；]|(?<!\d)\.(?!\d)", text):
        if re.search(r"梯度.{0,25}(?:表现良好|良好|稳定)|"
                     r"gradients?.{0,25}well[-\s]behaved", clause, re.I):
            if not re.search(r"初始化|初始(?:时|阶段|状态)|initiali[sz](?:ation|ed)", clause, re.I):
                return True
    return False


def unqualified_human_preference(text, cited):
    """Preserve both human evaluation and its explicit prompt-distribution scope."""
    if not any(item.source_type != "reference" and re.search(
        r"human evaluations? on our prompt distribution.{0,180}preferred",
        " ".join(item.quote.split()), re.I,
    ) for item in cited):
        return False
    for clause in re.split(r"[。;；]|(?<!\d)\.(?!\d)", text):
        if not re.search(r"更受(?:用户)?偏好|被偏好|更受欢迎|\bpreferred\b", clause, re.I):
            continue
        if not (re.search(r"人(?:类|工)(?:评估|评价|评测)|人评|human evaluat", clause, re.I)
                and re.search(r"(?:提示|prompt).{0,12}(?:分布|distribution)", clause, re.I)
                and re.search(r"文中|该(?:文|研究|评测)|本研究|our|their|paper|evaluated",
                              clause, re.I)):
            return True
    return False


def filter_review_publication(
    claims: list[AnswerClaim], evidence: list[EvidenceAnchor],
) -> tuple[list[AnswerClaim], list[str]]:
    """Apply existing guards in order; retain each rule's independent warning.

    Call after checked_claims, which has already validated every evidence ID.
    Schema-time checks remain separate: saved or legacy providers may bypass
    the thematic renderer, so publication must still validate their output.
    """
    anchors = {item.evidence_id: item for item in evidence}
    rules = (
        (
            lambda text, cited: opaque_paper_attribution(text),
            "已暂缓 {count} 条用内部P编号代替论文／方法名称的条目；保留原输出，不自动代换归属。",
        ),
        (
            lambda text, cited: mixed_source_shortage_note(text),
            "已暂缓 {count} 条将证据不足说明混入论文事实的条目；"
            "保留原输出供核查，不将可用事实和无依据说明拼成完整结论。",
        ),
        (
            unqualified_scaling_span,
            "已暂缓 {count} 条未保留部分趋势／数量级范围限定的陈述；"
            "不把缩放变量范围误写为损失下降幅度。",
        ),
        (
            unqualified_dimension_condition,
            "已暂缓 {count} 条遗漏长度与维度比较条件的速度陈述；"
            "请保留原文适用条件，不能仅写为短序列更快。",
        ),
        (
            unqualified_warmup_necessity,
            "已暂缓 {count} 条将预热动机扩大为无条件必要的陈述；"
            "请保留学习率条件或文中设置，不能由局部稳定性分析推断所有配置都必须预热。",
        ),
        (
            unsupported_combination_absence,
            "已暂缓 {count} 条缺少原文明确说明的组合未验证断言；"
            "摘录未出现不等于原论文未评估，需审校否定结论的来源范围。",
        ),
        (
            ungrounded_compute_increase,
            "已暂缓 {count} 条缺少计算量增加原文的陈述；"
            "IO改善的出处不能替代FLOP比较的出处。",
        ),
        (
            ungrounded_compute_nonreduction,
            "已暂缓 {count} 条没有实际引用FLOP比较依据的负向陈述；"
            "IO读写减少不能证明计算量不减少。",
        ),
        (
            unqualified_gradient_stability,
            "已暂缓 {count} 条遗漏初始化范围的梯度稳定性陈述；"
            "前一个模型的限定不能自动覆盖分号后的另一命题。",
        ),
        (
            unqualified_compute_comparator,
            "已暂缓 {count} 条遗漏比较基线或反向传播条件的FLOP陈述；"
            "不能把有条件的计算量比较扩展到全部阶段。",
        ),
        (
            unqualified_human_preference,
            "已暂缓 {count} 条遗漏人类评估或提示分布限定的偏好比较；"
            "不将文中评价扩展为普遍优劣。",
        ),
    )
    warnings = []
    for guard, message in rules:
        kept = [
            claim for claim in claims
            if not guard(claim.text, [anchors[key] for key in claim.evidence_ids])
        ]
        if len(kept) != len(claims):
            warnings.append(message.format(count=len(claims) - len(kept)))
        claims = kept
    return claims, warnings
