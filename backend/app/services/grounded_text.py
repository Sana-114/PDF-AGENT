"""Publish only checked claims, never the model's unchecked narrative."""

import re
import unicodedata
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor, GroundingAudit, GroundingRejection
from app.services.claim_semantics import claim_semantic_conflicts
from app.services.credit_name_completion import name_completion_conflicts
from app.services.retrieval_policy import requested_comparison_pair

NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.\-\u0370-\u03ff])[+-]?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?"
    r"(?:\s?(?:%|thousand|million|billion|trillion|万亿|亿|万|[KMBT]))?"
    r"(?![A-Za-z0-9_]|\.\d)",
    re.IGNORECASE,
)
INLINE_ANCHOR = re.compile(r"\[(?:[ERG]\d+)(?:[\s,]+[ERG]\d+)*\]", re.I)
PAGE_LOCATOR = re.compile(
    r"(?:物理)?第\s*(?P<zh>\d+)\s*页|"
    r"(?<![A-Za-z])(?:physical\s+)?(?:page|p\.)\s*(?P<en>\d+)(?!\d|\.\d)", re.I,
)
SCALES = {
    "k": 1000,
    "m": 1000000,
    "b": 1000000000,
    "t": 1000000000000,
    "万": 10000,
    "亿": 100000000,
    "万亿": 1000000000000,
}
DIFFERENCE = re.compile(
    r"(?<![\d.])(?P<left>\d+(?:\.\d+)?)\s*[-−]\s*"
    r"(?P<right>\d+(?:\.\d+)?)\s*=\s*(?P<result>[+-]?\d+(?:\.\d+)?)"
    r"(?![\d.])"
)
DERIVATION_CUE = re.compile(r"推导|计算|\bderived\b|\bcalculated\b", re.I)
ENGLISH_CARDINALS = dict(
    zip(
        (
            "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
            "fifteen sixteen seventeen eighteen nineteen twenty"
        ).split(),
        range(21),
        strict=True,
    )
)
# Word-spelled small counts are equivalent to digits, not extra inferred values.
# Exclude ordinals, articles (a/an), model-name fragments and scale arithmetic.
CARDINAL_WORD = re.compile(
    r"(?<![\w-])(?P<number>" + "|".join(ENGLISH_CARDINALS) + r")(?![\w-])"
    r"(?=\s+(?:[A-Za-z]+(?:-[A-Za-z]+)*\s+){0,3}"
    r"(?:exemplars?|examples?|demonstrations?|models?|layers?|tokens?|parameters?|GPUs?|"
    r"epochs?|iterations?|tasks?|languages?|steps?|stages?|actors?|heads?|samples?)\b)",
    re.I,
)
MULTIPLIER_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<number>\d+(?:\.\d+)?)\s*(?:x|×)(?![A-Za-z0-9_])", re.I
)
PERCENTILE_NUMBER = re.compile(
    r"(?:[~≈]\s*(?P<approx>\d+)(?:st|nd|rd|th)\b|"
    r"\b(?P<explicit>\d+)(?:st|nd|rd|th)\s+percentile\b)",
    re.I,
)
SCALED_QUANTITY = r"\d+(?:\.\d+)?\s*(?:million|billion|trillion|万亿|亿|万|[KMBT])(?![A-Za-z])"
ROUNDED_ALIAS = re.compile(
    rf"(?P<precise>{SCALED_QUANTITY})\s*[（(]\s*(?:约|approximately|about|~)\s*"
    rf"(?P<alias>{SCALED_QUANTITY})\s*[）)]",
    re.I,
)
CORRECTION_PATTERNS = [
    re.compile(
        rf"(?P<actual>{NUMERIC_FACT_PATTERN.pattern})\s*[,，]?\s*"
        rf"(?:而不是|而非|而不为|rather\s+than|not)\s*"
        rf"(?P<wrong>{NUMERIC_FACT_PATTERN.pattern})",
        re.I,
    ),
    re.compile(
        rf"(?:不是|并非|not)\s*(?P<wrong>{NUMERIC_FACT_PATTERN.pattern})\s*[,，]?\s*"
        rf"(?:而是|but(?:\s+rather)?|instead(?:\s+of)?\s+being)\s*"
        rf"(?P<actual>{NUMERIC_FACT_PATTERN.pattern})",
        re.I,
    ),
]
SOURCE_LIMITATION = re.compile(
    r"(?:所提供的|所引用的|提供的|当前|本次|检索到的)?(?:证据|摘录|原文)"
    r"[^。;；!?\n]{0,25}(?:没有|未|不)(?:明确)?(?:报告|提供|给出|说明|包含|提及)|"
    r"\b(?:provided|available|cited|retrieved|current)\s+(?:source\s+)?"
    r"(?:evidence|excerpts?|passages?)\b[^.;!?\n]{0,35}"
    r"\b(?:does?\s+not|do\s+not|never)\s+(?:explicitly\s+)?"
    r"(?:report|provide|specify|contain|state|give)\b",
    re.I,
)
STRUCTURAL_TUPLE_SEPARATOR = re.compile(
    r"(?P<field>\b(?:L|H|A|N|n_layers|n_heads|d_model)\s*=\s*\d+)"
    r",\s*(?P<count>\d+(?:\.\d+)?\s*(?:million|billion|[KMBT]))(?=\s*[),])", re.I,
)
NAMED_VARIANT_SHORTHAND = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<prefix>[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*-)"
    r"(?P<variants>\d+(?:\s*/\s*\d+)+)(?![A-Za-z0-9_/%-]|\.\d)"
)


def only_source_limitations(claims: Sequence[AnswerClaim]) -> bool:
    """A cited statement of missing evidence is still a non-answer.

    Deliberately narrow: do not label a scientific negative finding or a
    provenance caveat followed by a supported answer as a pure refusal.
    """
    return bool(claims) and all(
        SOURCE_LIMITATION.search(claim.text)
        and not re.search(r"但|然而|不过|而是|实际|\bbut\b|\bhowever\b", claim.text, re.I)
        and all(
            SOURCE_LIMITATION.search(sentence)
            for sentence in re.split(r"[。;；!?！？\n]|(?<!\d)\.(?!\d)", claim.text)
            if sentence.strip()
        )
        for claim in claims
    )


def _mask_supported_correction(text: str, evidence: Sequence[EvidenceAnchor]) -> str:
    """Ignore only an explicitly negated number paired with a source value.

    No exemption for a lone denial, speculation, or an unsupported alternative;
    published wording remains untouched and other affirmative values are checked.
    """
    text = unicodedata.normalize("NFKC", text)
    spans = []
    for pattern in CORRECTION_PATTERNS:
        for match in pattern.finditer(text):
            actual, wrong = (normalise_fact(match[key]) for key in ("actual", "wrong"))
            if actual != wrong and any(
                actual in _source_numeric_facts(item.quote) for item in evidence
            ):
                spans.append(match.span("wrong"))
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def normalise_fact(value: str) -> str:
    normalised = " ".join(value.casefold().replace(",", "").split())
    normalised = (
        normalised.replace("thousand", "k")
        .replace("million", "m")
        .replace("billion", "b")
        .replace("trillion", "t")
        .replace(" ", "")
    )
    numeric = re.fullmatch(r"([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)(万亿|亿|万|[kmbt%])?", normalised)
    if numeric:
        try:
            value = Decimal(numeric.group(1))
            unit = numeric.group(2)
            if len(normalised) > 80 or abs(value.adjusted()) > 30:
                return normalised
            with localcontext() as context:
                context.prec = 100
                if unit in SCALES:
                    return "quantity:" + format((value * SCALES[unit]).normalize(), "f")
                return format(value.normalize(), "f") + (unit or "")
        except InvalidOperation:
            pass
    return normalised


def _source_numeric_facts(text: str) -> set[str]:
    # Greek variable subscripts identify fields (beta_1), not extra numerical
    # assertions. Normalize math alphabets/subscripts before extracting values;
    # values after an equals sign remain subject to the same source check.
    text = unicodedata.normalize("NFKC", text)
    # A named structural tuple may omit the label of its last parameter count:
    # '(L=6,A=8,70M)'. That separator is not the thousands grouping of '8,70M'.
    # Ordinary comma-grouped quantities, e.g. 'parameters=12,110M', stay intact.
    text = STRUCTURAL_TUPLE_SEPARATOR.sub(r"\g<field> \g<count>", text)
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    word_counts = set()
    number_words = set(ENGLISH_CARDINALS) | {
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "trillion",
    }
    for match in CARDINAL_WORD.finditer(text):
        before = re.search(r"([A-Za-z-]+)\s+$", text[: match.start()])
        after = re.match(r"\s+([A-Za-z-]+)", text[match.end() :])
        if any(item and item.group(1).casefold() in number_words for item in (before, after)):
            continue  # Do not reduce 'twenty one' or 'eight hundred' to a small count.
        word_counts.add(str(ENGLISH_CARDINALS[match["number"].casefold()]))
    return (
        {normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(text)}
        | word_counts
        | {normalise_fact(match["number"]) for match in MULTIPLIER_NUMBER.finditer(text)}
        | {
            normalise_fact(match["approx"] or match["explicit"])
            for match in PERCENTILE_NUMBER.finditer(text)
        }
    )


def _expand_supported_variant_shorthand(text: str, evidence: Sequence[EvidenceAnchor]) -> str:
    """Audit-only expansion when one cited passage names every full identifier.

    Do not exempt ordinary ratios, unseen variants or measurements beside a name.
    The published statement and original quote are never rewritten.
    """
    def expand(match: re.Match) -> str:
        names = [match["prefix"] + value.strip() for value in match["variants"].split("/")]
        patterns = [re.compile(
            r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_/-]|\.\d)", re.I,
        ) for name in names]
        if any(all(pattern.search(unicodedata.normalize("NFKC", item.quote))
                   for pattern in patterns) for item in evidence):
            return " ".join(names)
        return match.group()

    return NAMED_VARIANT_SHORTHAND.sub(expand, unicodedata.normalize("NFKC", text))


def unsupported_numeric_facts(text: str, evidence: Sequence[EvidenceAnchor]) -> list[str]:
    text = _expand_supported_variant_shorthand(text, evidence)
    pages = {item.page_number for item in evidence}
    # Coordinates are provenance, not scientific measurements. Exempt ONLY an
    # explicit locator matching a cited page; an adjacent GPU count still needs
    # textual support. Never whitelist every number equal to a page number.
    text = PAGE_LOCATOR.sub(
        lambda match: " " if int(match["zh"] or match["en"]) in pages else match.group(), text,
    )
    def verify_rounded_alias(match: re.Match) -> str:
        precise, alias = (normalise_fact(match[key]) for key in ("precise", "alias"))
        unit = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*(million|billion|trillion|万亿|亿|万|[KMBT])", match["alias"], re.I
        )
        if (
            not unit
            or not precise.startswith("quantity:")
            or not alias.startswith("quantity:")
            or not any(precise in _source_numeric_facts(item.quote) for item in evidence)
        ):
            return match.group()
        scale = normalise_fact("1" + unit[2]).removeprefix("quantity:")
        with localcontext() as context:
            context.prec = 100
            exact, approximate = Decimal(precise[9:]), Decimal(alias[9:])
            quantum = Decimal(10) ** Decimal(unit[1]).as_tuple().exponent * Decimal(scale)
            rounded = (exact / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP) * quantum
            if (
                exact
                and rounded == approximate
                and abs(exact - approximate) / abs(exact) <= Decimal("0.05")
            ):
                # Validation only: keep the published exact number and labelled
                # rounded alias unchanged. Do not permit a rounded value alone.
                return match["precise"]
        return match.group()

    text = _mask_supported_correction(ROUNDED_ALIAS.sub(verify_rounded_alias, text), evidence)
    # Only explicit, labelled subtraction with both operands in ONE cited passage
    # may publish a new number. Arbitrary missing numbers are still rejected.
    derivation_errors = []
    if DERIVATION_CUE.search(text):

        def verify_difference(match: re.Match) -> str:
            left, right, result = (
                Decimal(match.group(name)) for name in ("left", "right", "result")
            )
            operands = {normalise_fact(match.group(name)) for name in ("left", "right")}
            with localcontext() as context:
                context.prec = max(50, len(match.group(0)) + 2)
                if left - right == result and any(
                    operands.issubset(_source_numeric_facts(item.quote)) for item in evidence
                ):
                    return ""
            derivation_errors.append("invalid_derivation:" + match.group(0))
            return match.group(0)

        text = DIFFERENCE.sub(verify_difference, text)
    facts = sorted(_source_numeric_facts(text))
    source = " ".join(
        [
            *(item.quote for item in evidence),
            *(item.document_title or "" for item in evidence),
            *(item.section or "" for item in evidence),
        ]
    )
    # PDF extraction can split a decimal into separate glyph runs ("0 . 98").
    source_facts = _source_numeric_facts(source)
    return derivation_errors + [fact for fact in facts if fact not in source_facts]


def _comparison_subject_mentioned(name: str, text: str) -> bool:
    """A component name alone is not attribution to its original paper.

    Keep actual contrasts and independent subject mentions under the dual-source
    rule. This is a narrow lexical distinction, not a general relation parser.
    """
    matches = list(re.finditer(
        r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.I
    ))
    if not matches:
        return False
    if re.search(r"相比|相较|对比|不同|基础上|而(?:非|是)|"
                 r"\b(?:whereas|unlike|compared|contrast)\b|\bbased on\b", text, re.I):
        return True
    return any(
        not re.match(r"\s*(?:适配器|组件|模块|adapters?\b|components?\b|modules?\b)",
                     text[match.end():], re.I)
        and not re.search(r"\badapters?\s*[（(]\s*$", text[:match.start()], re.I)
        for match in matches
    )


def audit_grounded_claims(
    claims: Sequence[GeneratedClaim], evidence: Sequence[EvidenceAnchor], *, question: str = "",
) -> tuple[list[AnswerClaim], GroundingAudit]:
    anchors = {item.evidence_id: item for item in evidence}
    accepted: list[AnswerClaim] = []
    rejections: list[GroundingRejection] = []
    counts: dict[str, int] = {}
    compared = requested_comparison_pair(question)
    originals = {item.document_id for item in evidence if item.source_type != "reference"}
    for index, claim in enumerate(claims):
        ids = list(dict.fromkeys(item for item in claim.evidence_ids if item in anchors))
        for document_id in {anchors[key].document_id for key in ids}:
            counts[document_id] = counts.get(document_id, 0) + 1
        inline_ids = re.findall(r"[ERG]\d+", " ".join(INLINE_ANCHOR.findall(claim.text)), re.I)
        text = INLINE_ANCHOR.sub("", claim.text).strip()
        reasons = []
        if not text:
            reasons.append("empty_statement")
        if not ids:
            reasons.append("missing_allowed_citation")
        if any(item.upper() not in ids for item in inline_ids):
            reasons.append("inline_citation_outside_declared_sources")
        cited = [anchors[item] for item in ids]
        if text and ids:
            if (len(compared) == 2 and len(originals) == 2
                    and all(_comparison_subject_mentioned(name, text) for name in compared)
                    and {item.document_id for item in cited if item.source_type != "reference"}
                    != originals):
                reasons.append("semantic_conflict:comparison_original_source_omitted")
            reasons.extend("unsupported_numeric_fact:" + value
                           for value in unsupported_numeric_facts(text, cited))
            reasons.extend("semantic_conflict:" + reason
                           for reason in claim_semantic_conflicts(text, cited, evidence))
            reasons.extend("semantic_conflict:" + reason
                           for reason in name_completion_conflicts(question, text, cited))
        if reasons:
            rejections.append(GroundingRejection(
                claim_index=index, evidence_ids=ids, reasons=reasons,
            ))
            continue
        accepted.append(AnswerClaim(text=text, evidence_ids=ids))
    return accepted, GroundingAudit(
        input_claim_count=len(claims), accepted_claim_count=len(accepted),
        document_claim_counts=counts, rejections=rejections,
    )


def checked_claims(
    claims: Sequence[GeneratedClaim], evidence: Sequence[EvidenceAnchor]
) -> tuple[list[AnswerClaim], int]:
    """Compatibility wrapper sharing the same source checks and diagnostics."""
    accepted, audit = audit_grounded_claims(claims, evidence)
    return accepted, len(audit.rejections)


def grounding_rejection_warnings(audit: GroundingAudit) -> list[str]:
    """Human-readable shared diagnostics; no model text is rewritten or republished."""
    labels = {
        "empty_statement": "陈述为空",
        "missing_allowed_citation": "没有当前证据范围内的有效引用",
        "inline_citation_outside_declared_sources": "正文引用与该条声明的引用不一致",
        "gradient_initialization_phase_omitted": "未保留初始化阶段限定",
        "gradient_near_output_parameter_scope_omitted": "未保留输出层附近参数的范围",
        "gradient_expectation_qualifier_omitted": "未保留期望梯度限定",
        "sampling_temperature_task_mismatch": "采样温度与实际引用中的任务归属不一致",
        "paper_not_limited_to_analysis": "原文包含算法和实验，不能概括为只做理论分析",
        "restricted_attention_path_direction_reversed": "限制注意力的最大路径变化方向与原文相反",
        "closed_form_policy_object_omitted": "闭式结论未明确对应的策略对象",
        "policy_expression_not_analytic_model_fit": "策略闭式表达不能替代模型参数的训练求解",
        "lm_sampling_finetuning_scope_omitted": "免采样结论未保留语言模型采样与微调阶段限定",
        "sampling_avoidance_missing_direct_cited_support": "免采样结论缺少直接引用的原文支持",
        "shared_constraint_missing_from_each_original": "共同约束的声明未得到每份原文分别支持",
        "performance_gain_not_memory_gain": "准确率增益的大小不能转述成内存节省幅度",
        "comparison_original_source_omitted": "该跨文声明未分别引用两份所选原文",
    }

    def describe(reason: str) -> str:
        if reason.startswith("unsupported_numeric_fact:"):
            return "所引摘录未支持数值 " + reason.split(":", 1)[1]
        if reason.startswith("semantic_conflict:"):
            code = reason.split(":", 1)[1]
            return labels.get(code, "可检测的来源语义冲突：" + code)
        return labels.get(reason, reason)

    return [f"防护暂缓原第 {item.claim_index + 1} 条："
            + "；".join(describe(reason) for reason in item.reasons)
            + "。其他有据陈述继续保留。" for item in audit.rejections]


def render_checked_claims(claims: Sequence[AnswerClaim]) -> str:
    if not claims:
        return "当前原文证据不足以发布有依据的结论，请补充相关文献或缩小问题范围。"
    return "\n\n".join(f"{claim.text} [{' '.join(claim.evidence_ids)}]" for claim in claims)
