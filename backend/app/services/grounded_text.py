"""Publish only checked claims, never the model's unchecked narrative."""

import re
from collections.abc import Sequence

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor

NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![\w-])\d+(?:[.,]\d+)*(?:\s?(?:%|[KMB]|thousand|million|billion))?(?![\w])",
    re.IGNORECASE,
)
INLINE_ANCHOR = re.compile(r"\[(?:[ER]\d+)(?:[\s,]+[ER]\d+)*\]", re.I)


def normalise_fact(value: str) -> str:
    normalised = " ".join(value.casefold().replace(",", "").split())
    return (
        normalised.replace("thousand", "k")
        .replace("million", "m")
        .replace("billion", "b")
        .replace(" ", "")
    )


def unsupported_numeric_facts(text: str, evidence: Sequence[EvidenceAnchor]) -> list[str]:
    facts = list(dict.fromkeys(normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(text)))
    source = " ".join(
        [
            *(item.quote for item in evidence),
            *(item.document_title or "" for item in evidence),
            *(item.section or "" for item in evidence),
        ]
    )
    # PDF extraction can split a decimal into separate glyph runs ("0 . 98").
    source = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", source)
    source_facts = {normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(source)}
    return [fact for fact in facts if fact not in source_facts]


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
