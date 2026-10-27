"""Narrow, source-derived checks for explicit Latin-initial name completion."""

import re
from collections.abc import Sequence

from app.schemas.agent import EvidenceAnchor
from app.services.author_evidence import CONTRIBUTOR_SECTION, _name_key

PREFIX = re.compile(r"\b[A-Z][A-Za-z'’-]+\s+(?:[A-Z]\.\s*)+")
REQUEST = re.compile(r"完整姓名|全名|姓氏|full\s+name|surname", re.I)
ASSIGNMENT = re.compile(
    r"(?:完整姓名|全名|姓氏)\s*(?:为|是|[:：])\s*[“\"`]?"
    r"(?P<name>[A-Za-zÀ-ÖØ-öø-ÿĀ-ž][A-Za-zÀ-ÖØ-öø-ÿĀ-ž. '’-]*)"
)


def completion_candidates(
    question: str,
    evidence: Sequence[EvidenceAnchor],
) -> dict[str, list[str]]:
    prefixes = {match.group().strip() for match in PREFIX.finditer(question)}
    if not REQUEST.search(question) or len(prefixes) != 1:
        return {}
    prefix = next(iter(prefixes))
    result: dict[str, list[str]] = {}
    for item in evidence:
        section = item.section or ""
        if not section.startswith(CONTRIBUTOR_SECTION + " · "):
            continue
        # Only structured role source nodes, not prose mentioning the same name.
        if not item.quote.startswith("Contribution role (structured group): "):
            continue
        role = section.split(" · ", 1)[1]
        raw = " ".join(item.quote.split("\n", 2)[-1].split())
        if raw.startswith(role + " "):
            raw = raw[len(role) :].strip()
        for name in raw.split(","):
            name = name.strip()
            if name.startswith(prefix + " "):
                result.setdefault(name, []).append(item.evidence_id)
    return result


def name_completion_guidance(question: str, evidence: Sequence[EvidenceAnchor]) -> str:
    names = completion_candidates(question, evidence)
    if len(names) != 1:
        return ""
    name, ids = next(iter(names.items()))
    return (
        "\nName-completion constraint: answer the ONE person's full name, not the surname "
        "of the next comma-separated person. The existing cited source gives the complete "
        f"name {name} ({', '.join(dict.fromkeys(ids))}). Cite those original passages; "
        "this hint is not an additional source and creates no new author rank."
    )


def credit_scope_guidance(question: str, evidence: Sequence[EvidenceAnchor]) -> str:
    roles = [item.section.split(" · ", 1)[1] for item in evidence
             if (item.section or "").startswith(CONTRIBUTOR_SECTION + " · ")]
    if not roles or not (
        re.search(r"贡献|署名|名单|分组|\b(?:authors?|contributors?|members?)\b", question, re.I)
        or any(role.casefold() in question.casefold() for role in roles)
    ):
        return ""
    return (
        "\nCredit-list scope: answer only the requested credit, names, membership, "
        "name completion or missing affiliation mapping and its original page locations. "
        "Do not append unrelated author-order conventions, category definitions or "
        "biographical/affiliation details from elsewhere. A role list is not global author rank. "
        "If the question limits the answer to front matter, do not expand to acknowledgments."
    )


def name_completion_conflicts(
    question: str,
    text: str,
    evidence: Sequence[EvidenceAnchor],
) -> list[str]:
    names = completion_candidates(question, evidence)
    if len(names) != 1:
        return []
    expected = next(iter(names))
    for match in ASSIGNMENT.finditer(text):
        value = match["name"].strip(" .")
        complete = match.group().startswith(("完整姓名", "全名"))
        if complete and _name_key(value) != _name_key(expected):
            return ["name_completion_target_mismatch"]
        if not complete and not _name_key(expected).endswith(_name_key(value)):
            return ["name_completion_target_mismatch"]
    return []
