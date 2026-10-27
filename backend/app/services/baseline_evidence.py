"""Find bounded bibliography candidates from an actually quoted baseline citation.

This does not resolve model aliases or infer a context value. It only follows an
author-year reference already present in a context-configuration body passage.
"""

import re
import unicodedata

from app.schemas.agent import EvidenceAnchor
from app.services.evidence_fields import CONTEXT_ASSIGNMENT

BASELINE_CITATION = re.compile(
    r"\b(?:follow(?:s|ed)?|based\s+on)\b[^()]{0,160}\(\s*"
    r"(?P<author>[A-Z][A-Za-z-]{2,40})(?:\s+et\s+al\.)?\s*,\s*"
    r"(?P<year>(?:19|20)\d{2})[a-z]?\s*\)",
    re.I,
)


def baseline_citations(evidence: list[EvidenceAnchor]) -> list[tuple[str, str, str]]:
    result = []
    for item in evidence:
        text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", item.quote)
        text = " ".join(text.split())
        if item.source_type != "text" or not any(
            match["last"] for match in CONTEXT_ASSIGNMENT.finditer(text)
        ):
            continue
        for match in BASELINE_CITATION.finditer(text):
            entry = (item.document_id, match["author"], match["year"])
            if entry not in result:
                result.append(entry)
    return result[:3]


def matches_baseline_reference(item: EvidenceAnchor, author: str, year: str) -> bool:
    return (
        item.source_type == "reference"
        and bool(re.search(r"(?<![\w-])" + re.escape(author) + r"(?![\w-])", item.quote, re.I))
        and bool(re.search(r"(?<!\d)" + re.escape(year) + r"(?!\d)", item.quote))
    )


def baseline_reference_bridges(
    comparisons: list[EvidenceAnchor], evidence: list[EvidenceAnchor]
) -> list[set[str]]:
    """Identify available author-year entries matching a unique OTHER source title.

    No model alias or quantity is assigned. Ambiguous titles are not identity
    proof. Return the original candidate IDs, never append them to a claim.
    """

    def key(text):
        return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())

    titles = {}
    for item in evidence:
        if item.document_title and len(key(item.document_title)) >= 12:
            titles.setdefault(item.document_id, set()).add(key(item.document_title))
    result = []
    for document_id, author, year in baseline_citations(comparisons):
        candidates = []
        for item in evidence:
            if item.document_id != document_id or not matches_baseline_reference(
                item, author, year
            ):
                continue
            targets = {
                doc
                for doc, names in titles.items()
                if doc != document_id and len(names) == 1 and next(iter(names)) in key(item.quote)
            }
            if len(targets) == 1:
                candidates.append((item.evidence_id, next(iter(targets))))
        # Conflicting entries identifying different selected papers must not be
        # reduced to whichever happens to rank highest.
        if candidates and len({target for _, target in candidates}) == 1:
            result.append({eid for eid, _ in candidates})
    return result
