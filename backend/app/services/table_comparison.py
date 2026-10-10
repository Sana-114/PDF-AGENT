"""Deterministic BLEU comparisons only for an unambiguous source table and named variant."""

import re
from decimal import Decimal, localcontext

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor


def table_bleu_claims(question: str, evidence: list[EvidenceAnchor]) -> list[GeneratedClaim]:
    if not re.search(r"BLEU", question, re.I) or not re.search(r"提升|差值|improv", question, re.I):
        return []
    tasks = []
    if re.search(r"English[\s-]*to[\s-]*German|EN[\s-]*DE|英.{0,4}德", question, re.I):
        tasks.append("EN-DE")
    if re.search(r"English[\s-]*to[\s-]*French|EN[\s-]*FR|英.{0,4}法", question, re.I):
        tasks.append("EN-FR")
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
