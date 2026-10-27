"""Bounded Adam beta-field projection; no defaults, gold answers or model inference.

Only explicit, consistent single-document assignments can supply a missing field.
Ambiguous alternatives, negations and damaged numbers cannot become source facts.
"""

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor

BETA_LABEL = r"(?:β|\\?beta)\s*[_ ]?\s*\{?(?P<field>[12])\}?(?!\d)"
LABEL = re.compile(BETA_LABEL, re.I)
ASSIGNMENT = re.compile(
    BETA_LABEL + r"\s*(?:(?:的\s*)?(?:取值|设置|值)\s*)?"
    r"(?:=|为|是|is|was|[:：])\s*(?P<value>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?!\w|\.\d)", re.I,
)
ADAM = re.compile(r"(?<![A-Za-z])Adam(?:W)?(?![A-Za-z])", re.I)
UNCERTAIN = re.compile(r"不(?:是|为|等于)|并非|未设|可能|約|约|大约|默认|如果|"
                       r"\b(?:not|maybe|approximately|about|default|usually|typically|if)\b", re.I)
ALTERNATIVE = re.compile(r"^\s*(?:或|至|到|[~～−–-]\s*\d|[,，]\s*\d|\b(?:or|to)\b|±|\+/-)", re.I)


def normalized(text: str) -> str:
    return re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", unicodedata.normalize("NFKC", text))


def _assignments(text: str):
    text = normalized(text)
    for match in ASSIGNMENT.finditer(text):
        # Look only within this clause, not a denial of a different preceding field.
        prefix = re.split(r"[。;；\n]|(?<!\d)\.(?!\d)|[,，]", text[:match.start()])[-1]
        ambiguous = bool(UNCERTAIN.search(prefix) or ALTERNATIVE.search(text[match.end():]))
        try:
            value = Decimal(match["value"])
            if not value.is_finite() or not 0 <= value < 1:
                ambiguous = True
        except InvalidOperation:
            ambiguous, value = True, Decimal(-1)
        yield match["field"], value, match["value"], ambiguous


def explicit_beta_sources(evidence: Sequence[EvidenceAnchor]):
    values: dict[str, set[Decimal]] = {}
    sources: dict[tuple[str, Decimal], list[EvidenceAnchor]] = {}
    damaged = set()
    if len({item.document_id for item in evidence}) != 1:
        return {}
    for item in evidence:
        if item.source_type == "reference" or not ADAM.search(item.quote):
            continue
        for field, value, _, ambiguous in _assignments(item.quote):
            if ambiguous:
                damaged.add(field)
                continue
            values.setdefault(field, set()).add(value)
            sources.setdefault((field, value), []).append(item)
    return {field: (next(iter(found)), sources[(field, next(iter(found)))])
            for field, found in values.items() if len(found) == 1 and field not in damaged}


def optimizer_beta_conflicts(text: str, cited: Sequence[EvidenceAnchor]) -> list[str]:
    """Reject identifiable field/value swaps even when both numbers occur in a quote."""
    expected = explicit_beta_sources(cited)
    for field, value, _, ambiguous in _assignments(text):
        if field in expected and (ambiguous or value != expected[field][0]):
            return ["optimizer_beta_field_value_mismatch"]
    return []


def missing_optimizer_beta_claims(question: str, evidence: Sequence[EvidenceAnchor],
                                  published: Sequence[AnswerClaim]) -> list[GeneratedClaim]:
    if not ADAM.search(question):
        return []
    requested = list(dict.fromkeys(
        match["field"] for match in LABEL.finditer(normalized(question))
    ))
    supported = explicit_beta_sources(evidence)
    anchors = {item.evidence_id: item for item in evidence}
    result = []
    for field in requested:
        if field not in supported:
            continue
        value, sources = supported[field]
        covered = any(
            any(label == field and actual == value and not ambiguous
                for label, actual, _, ambiguous in _assignments(claim.text))
            and field in explicit_beta_sources(
                [anchors[key] for key in claim.evidence_ids if key in anchors]
            )
            for claim in published
        )
        if not covered:
            result.append(GeneratedClaim(
                text=f"原文明确设置 Adam 的 β{field} = {format(value, 'f')}。",
                evidence_ids=[sources[0].evidence_id],
            ))
    return result
