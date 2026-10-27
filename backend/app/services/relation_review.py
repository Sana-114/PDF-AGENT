"""Reject-only review of causal and mathematical relations with original citations."""

import re

from app.llm.base import LLMConfigurationError, LLMResponseError
from app.llm.claim_review import validate_verdicts

CAUSAL_LINK = re.compile(
    r"由此|因此|由于|因为|从而|因而|导致|使得|致使|得益于|归因于|"
    r"\b(?:therefore|hence|thus|because|enables?|allows?|causes?)\b|"
    r"\bas a result\b|\bdue to\b|\bleads? to\b|\bresults? in\b",
    re.I,
)


MATHEMATICAL_RELATION = re.compile(
    r"闭式|解析解|最优策略|重参数化|参数化|等价|等同|"
    r"\bclosed[- ]form\b|\banalytic(?:al)? solution\b|\boptimal polic(?:y|ies)\b|"
    r"\b(?:re)?parametri[sz](?:ation|e|ed)\b|\bequivalent\b",
    re.I,
)


async def review_relational_claims(provider, claims, evidence):
    """One bounded review, no rewriting/retries; failed review withholds targets."""
    indices = [i for i, claim in enumerate(claims)
               if CAUSAL_LINK.search(claim.text) or MATHEMATICAL_RELATION.search(claim.text)]
    review = getattr(provider, "review_claims", None)
    if not indices or not callable(review):
        return claims, None
    targets = [claims[i] for i in indices]
    try:
        verdicts = validate_verdicts(
            {"verdicts": await review(targets, evidence)}, len(targets)
        )
        accepted = {indices[v["claim_index"]] for v in verdicts if v["status"] == "supported"}
        audit = {"status": "checked", "claim_indices": indices, "verdicts": verdicts}
    except (LLMConfigurationError, LLMResponseError):
        accepted = set()
        audit = {"status": "unavailable", "claim_indices": indices, "verdicts": []}
    return [c for i, c in enumerate(claims) if i not in indices or i in accepted], audit
