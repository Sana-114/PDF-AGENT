"""Deterministic BLEU comparisons only for an unambiguous source table and named variant."""

import re
from decimal import Decimal, localcontext

from app.llm.base import GeneratedClaim
from app.schemas.agent import AnswerClaim, EvidenceAnchor


def _bleu_tasks(text: str) -> list[str]:
    tasks = []
    if re.search(r"English[\s-]*to[\s-]*German|EN[\s-]*DE|英.{0,4}德", text, re.I):
        tasks.append("EN-DE")
    if re.search(r"English[\s-]*to[\s-]*French|EN[\s-]*FR|英.{0,4}法", text, re.I):
        tasks.append("EN-FR")
    return tasks


BLEU_REFUSAL = re.compile(
    r"(?:未|没有|缺少|无法|不能|不足以)[^。;；\n]{0,70}(?:给出|提供|计算|确定|确认)|未知|"
    r"\b(?:unknown|unavailable)\b|\b(?:cannot|can't|unable\s+to)\s+"
    r"(?:be\s+)?(?:calculat\w*|giv\w*|provid\w*|determin\w*)", re.I
)
BLEU_COMPARISON_FIELD = re.compile(
    r"此前|之前|旧最佳|最佳|提升|差值|\b(?:previous|prior|baseline|best|difference|improvement)\b",
    re.I,
)
EXCERPT_LIMITATION = re.compile(
    r"该段|这段|此段|该摘录|这条摘录|所引段落|"
    r"\bthis\s+(?:(?:quoted|cited)\s+)?(?:excerpt|passage)\b", re.I
)


def remove_table_bleu_refusals(question: str, claims: list[AnswerClaim],
                               proof: list[GeneratedClaim]) -> tuple[list[AnswerClaim], int]:
    """Reject a bounded denial only after the full checked table proof is published.

    Keep other-task/field refusals and excerpt-local caveats. This is not a
    general semantic contradiction check or a verifier of all table assignments.
    The caller supplies only table_bleu_claims(), never user-authored proofs.
    """
    tasks = _bleu_tasks(question)
    if len(tasks) != 1 or not table_bleu_proof_published(claims, proof):
        return claims, 0
    proof_ids = {key for item in proof for key in item.evidence_ids}
    proof_text = {item.text for item in proof}
    retained = []
    for claim in claims:
        claim_tasks = _bleu_tasks(claim.text)
        conflict = (
            claim.text not in proof_text
            and bool(proof_ids.intersection(claim.evidence_ids))
            and (not claim_tasks or claim_tasks == tasks)
            and bool(re.search(r"BLEU", claim.text, re.I))
            and bool(BLEU_COMPARISON_FIELD.search(claim.text))
            and bool(BLEU_REFUSAL.search(claim.text))
            and not EXCERPT_LIMITATION.search(claim.text)
        )
        if not conflict:
            retained.append(claim)
    return retained, len(claims) - len(retained)


def table_bleu_proof_published(claims: list[AnswerClaim], proof: list[GeneratedClaim]) -> bool:
    """One shared publication condition for harness audits and saved-output replay."""
    return len(proof) == 3 and all(any(
        claim.text == item.text and set(claim.evidence_ids) == set(item.evidence_ids)
        for claim in claims) for item in proof)


def table_bleu_claims(question: str, evidence: list[EvidenceAnchor]) -> list[GeneratedClaim]:
    if not re.search(r"BLEU", question, re.I) or not re.search(r"提升|差值|improv", question, re.I):
        return []
    tasks = _bleu_tasks(question)
    variant = re.search(r"([A-Za-z][A-Za-z0-9_-]*)\s*\((big|base|large|small)\)", question, re.I)
    if len(tasks) != 1 or not variant:
        return []
    family, size = variant.groups()
    target = re.sub(r"\W", "", family + size).casefold()
    candidates = {}
    for item in evidence:
        if item.source_type != "table":
            continue
        rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
                for line in item.quote.splitlines() if line.strip().startswith("|")]
        headers = [row for row in rows if tasks[0] in [cell.upper() for cell in row]]
        if not headers:
            continue
        # Multi-level header: choose the task ONLY within the BLEU column span.
        metric_headers = [row for row in rows if "BLEU" in [cell.upper() for cell in row]]
        if not metric_headers:
            continue
        metric_header = metric_headers[0]
        start = next(index for index, cell in enumerate(metric_header) if cell.upper() == "BLEU")
        end = next((index for index in range(start + 1, len(metric_header))
                    if metric_header[index]), len(metric_header))
        columns = [index for index, cell in enumerate(headers[0])
                   if cell.upper() == tasks[0] and start <= index < end]
        if len(columns) != 1:
            continue
        column = columns[0]
        scores, targets = [], []
        for row in rows:
            if len(row) <= column or not re.fullmatch(r"\d+(?:\.\d+)?", row[column]):
                continue
            label = row[0]
            value = Decimal(row[column])
            if not 0 <= value <= 100:
                continue
            name = re.sub(r"\W", "", label.replace("model", "")).casefold()
            if name == target:
                targets.append((label, value))
            elif not re.match(re.escape(family) + r"(?:\s|\(|$)", label, re.I):
                scores.append((label, value))
        if len(targets) != 1 or not scores:
            continue
        previous_label, previous = max(scores, key=lambda pair: pair[1])
        label, value = targets[0]
        with localcontext() as context:
            context.prec = 100
            difference = value - previous
        signature = (item.document_id, tasks[0], label, str(value), previous_label, str(previous))
        candidates[signature] = (item, label, value, previous_label, previous, difference)
    if len(candidates) != 1:
        return []  # Conflicting tables/versions require clarification, not silent selection.
    item, label, value, previous_label, previous, difference = next(iter(candidates.values()))
    citation = [item.evidence_id]
    return [
        GeneratedClaim(text=f"表内 {label} 的 {tasks[0]} BLEU 为 {value}。", evidence_ids=citation),
        GeneratedClaim(
            text=f"该表此前参照模型中，最高 {tasks[0]} BLEU 为 {previous}"
            f"（{previous_label}）；范围为表内比较，未排除 ensemble 模型。", evidence_ids=citation
        ),
        GeneratedClaim(text=f"推导（同一表、同一任务）：{value} - {previous} = {difference} BLEU。",
                       evidence_ids=citation),
    ]
