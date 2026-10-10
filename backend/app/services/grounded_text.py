"""Publish only checked claims, never the model's unchecked narrative."""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, localcontext

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor

NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])[+-]?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?"
    r"(?:\s?(?:%|thousand|million|billion|万亿|亿|万|[KMB]))?"
    r"(?![A-Za-z0-9_]|\.\d)",
    re.IGNORECASE,
)
INLINE_ANCHOR = re.compile(r"\[(?:[ER]\d+)(?:[\s,]+[ER]\d+)*\]", re.I)
SCALES = {"k": 1000, "m": 1000000, "b": 1000000000,
          "万": 10000, "亿": 100000000, "万亿": 1000000000000}
DIFFERENCE = re.compile(
    r"(?<![\d.])(?P<left>\d+(?:\.\d+)?)\s*[-−]\s*"
    r"(?P<right>\d+(?:\.\d+)?)\s*=\s*(?P<result>[+-]?\d+(?:\.\d+)?)"
    r"(?![\d.])"
)
DERIVATION_CUE = re.compile(r"推导|计算|\bderived\b|\bcalculated\b", re.I)


def normalise_fact(value: str) -> str:
    normalised = " ".join(value.casefold().replace(",", "").split())
    normalised = (
        normalised.replace("thousand", "k")
        .replace("million", "m")
        .replace("billion", "b")
        .replace(" ", "")
    )
    numeric = re.fullmatch(r"([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)(万亿|亿|万|[kmb%])?", normalised)
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
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    return {normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(text)}


def unsupported_numeric_facts(text: str, evidence: Sequence[EvidenceAnchor]) -> list[str]:
    # Only explicit, labelled subtraction with both operands in ONE cited passage
    # may publish a new number. Arbitrary missing numbers are still rejected.
    derivation_errors = []
    if DERIVATION_CUE.search(text):
        def verify_difference(match: re.Match) -> str:
            left, right, result = (Decimal(match.group(name))
                                   for name in ("left", "right", "result"))
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
    facts = list(dict.fromkeys(normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(text)))
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


def checked_claims(
    claims: Sequence[GeneratedClaim], evidence: Sequence[EvidenceAnchor]
) -> tuple[list[AnswerClaim], int]:
    anchors = {item.evidence_id: item for item in evidence}
    accepted: list[AnswerClaim] = []
    for claim in claims:
        ids = list(dict.fromkeys(item for item in claim.evidence_ids if item in anchors))
        inline_ids = re.findall(r"[ER]\d+", " ".join(INLINE_ANCHOR.findall(claim.text)), re.I)
        text = INLINE_ANCHOR.sub("", claim.text).strip()
        if not text or not ids or any(item.upper() not in ids for item in inline_ids):
            continue
        if unsupported_numeric_facts(text, [anchors[item] for item in ids]):
            continue
        accepted.append(AnswerClaim(text=text, evidence_ids=ids))
    return accepted, len(claims) - len(accepted)


def render_checked_claims(claims: Sequence[AnswerClaim]) -> str:
    if not claims:
        return "当前原文证据不足以发布有依据的结论，请补充相关文献或缩小问题范围。"
    return "\n\n".join(f"{claim.text} [{' '.join(claim.evidence_ids)}]" for claim in claims)
