"""Direct original-sentence fallback for normalization modifications, not inference."""

import re
from collections.abc import Sequence

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.claim_semantics import NORMALIZATION

CHANGE_QUERY = re.compile(r"调整|改动|修改|移|增加|change|modif|move|add", re.I)
MODIFICATION = re.compile(
    r"\b(?:was|were|is|has\s+been)\s+(?P<action>moved|relocated|added)\b", re.I,
)
MOVE = re.compile(r"移动|移到|移至|挪|\b(?:move\w*|relocat\w*)\b", re.I)
ADD = re.compile(r"增加|添加|加入|\badd\w*\b", re.I)


def missing_normalization_quotes(question: str, evidence: Sequence[EvidenceAnchor],
                                  published: Sequence[AnswerClaim]) -> list[GeneratedClaim]:
    if not NORMALIZATION.search(question) or not CHANGE_QUERY.search(question):
        return []
    result, seen = [], set()
    for item in evidence:
        if item.source_type not in {"text", "formula"}:
            continue
        quote = " ".join(item.quote.split())
        # Preserve bibliographic abbreviations and decimal numbers inside the
        # quoted sentence. PDF line wrapping is whitespace, not a new statement.
        for sentence in re.split(r"(?<!\bal)(?<!\betc)\.(?!\d)\s+|[!?。;；]", quote, flags=re.I):
            norm = NORMALIZATION.search(sentence)
            actions = list(MODIFICATION.finditer(sentence))
            if not norm or not actions or not any(
                0 <= action.start() - norm.end() <= 80 for action in actions
            ):
                continue
            if len(sentence) > 1200 or re.search(
                r"\b(?:if|might|would|should|hypothetical)\b", sentence, re.I,
            ):
                continue
            required = {
                "add" if match["action"].lower() == "added" else "move" for match in actions
            }
            covered = set()
            for claim in published:
                if (item.evidence_id not in claim.evidence_ids
                        or not NORMALIZATION.search(claim.text)):
                    continue
                covered.update(name for name, pattern in (("move", MOVE), ("add", ADD))
                               if pattern.search(claim.text))
            if required <= covered or sentence in seen:
                continue
            seen.add(sentence)
            # Only format a verbatim sentence from a cited passage. No target,
            # origin placement or model identity is inferred or filled here.
            result.append(GeneratedClaim(
                text=f"位置调整的原文记载：“{sentence.strip()}.”", evidence_ids=[item.evidence_id]
            ))
    return result
