"""Query-directed field coverage, without fixed paper names, pages or answers."""

import re

from app.schemas.agent import EvidenceAnchor
from app.services.grounded_text import normalise_fact

CONTEXT_QUERY = re.compile(r"上下文|context\s+(?:window|size|length)|n\s*_?\s*ctx", re.I)
CONTEXT_VALUE = re.compile(
    r"(?:context\s+(?:window|size|length)|n\s*_?\s*ctx|上下文(?:窗口|长度))"
    r"[^\d。.;；\n]{0,80}?\d+(?:\s*(?:to|[-→])\s*\d+)?"
    r"\s*(?:个?\s*tokens?\b|个?\s*词元)", re.I
)
CONTEXT_FACET = "model configuration context window context size n_ctx tokens"
PARAMETER_QUERY = re.compile(
    r"参数量|参数规模|模型参数|\bparameters?\b|parameter[- ]count|n_?params|model size", re.I
)
PARAMETER_FACET = "model architecture parameter count nparams parameters layers model sizes"
CONTEXT_ASSIGNMENT = re.compile(
    r"(?:context\s+(?:window|size|length)|n\s*_?\s*ctx|上下文(?:窗口|长度|大小|规模)?)"
    r"\s*(?:(?:of|is|was|from|equals|为|是|由|从|大小为|长度为)\s*)?[:：=]?\s*"
    r"(?:n\s*_?\s*ctx\s*=?\s*)?(?P<first>\d+)"
    r"(?:\s*(?:to|增加到|扩大到|到|至|→)\s*(?P<last>\d+))?", re.I
)
COUNT = r"\d+(?:\.\d+)?\s*(?:million|billion|万亿|亿|万|[MB])(?![A-Za-z])"
PARAMETER_QUANTITY = re.compile(COUNT, re.I)
PARAMETER_LIST = re.compile(
    rf"(?:parameter\s+counts?|parameters?|参数量|参数规模)"
    rf"\s*(?:分别为|依次为|are|为|是)?\s*[:：]?\s*"
    rf"(?P<values>{COUNT}(?:\s*(?:[,，、]|and\b|和|以及)\s*{COUNT})+)", re.I
)
PARAMETER_PROSE = re.compile(
    rf"(?P<before>{COUNT})\s*[- ]?\s*(?:(?:trainable|total|model|learnable)\s+)?"
    r"(?:parameters?\b|(?:个)?参数)"
    rf"|(?:parameter\s*count|n_?params|参数量|参数规模|number of parameters|parameters?)"
    rf"\s*(?:(?:is|are|of|from|为|是|约为|从)\s*)?[:：=]?\s*(?P<after>{COUNT})"
    rf"(?:\s*(?:to|到|至|→)\s*(?P<after_last>{COUNT}))?", re.I
)
PARAMETER_HEADER = re.compile(
    r"(?:\bparameters\b|\bn_?params\b|参数量)\s*[|:：]?\s*"
    r"(?:n_?layers|layers|d_?model|层数)|(?:model\s+name|模型)\s*\|\s*参数量", re.I
)
EQUIVALENCE = re.compile(
    r"(?:smallest|largest|baseline|original|最小|最大|基线|原始)"
    r"[^。.;；\n]{0,100}(?:equivalent|same (?:size|model) as|等价|等同)", re.I
)


def context_values(item: EvidenceAnchor) -> set[str]:
    if item.source_type in {"reference", "figure"}:
        return set()
    values = set()
    for match in CONTEXT_ASSIGNMENT.finditer(" ".join(item.quote.split())):
        values.add(normalise_fact(match["first"]))
        if match["last"]:
            values.add(normalise_fact(match["last"]))
    return values


def parameter_values(item: EvidenceAnchor) -> set[str]:
    if item.source_type in {"reference", "figure"}:
        return set()
    text = " ".join(item.quote.split())
    values = {normalise_fact(match["before"] or match["after"])
              for match in PARAMETER_PROSE.finditer(text)}
    values.update(normalise_fact(match["after_last"])
                  for match in PARAMETER_PROSE.finditer(text) if match["after_last"])
    values.update(normalise_fact(value) for match in PARAMETER_LIST.finditer(text)
                  for value in PARAMETER_QUANTITY.findall(match["values"]))
    if PARAMETER_HEADER.search(text):
        values.update(normalise_fact(value) for value in PARAMETER_QUANTITY.findall(text))
    # A labelled list without column headers is also an explicit count source.
    for match in re.finditer(rf"\bparameters\s+((?:{COUNT}\s*)+)", text, re.I):
        values.update(normalise_fact(value) for value in PARAMETER_QUANTITY.findall(match[1]))
    return values


def parameter_statement(item: EvidenceAnchor) -> bool:
    return bool(parameter_values(item))


def context_statement(item: EvidenceAnchor) -> bool:
    # A table row containing d_model=2048 or a caption saying 'contexts are 768'
    # is not a context-window definition. The value must follow the field wording.
    return bool(context_values(item))


def select_field_evidence(question: str, evidence: list[EvidenceAnchor], top_k: int,
                          min_score: float) -> list[EvidenceAnchor]:
    """Reserve bounded strong field statements and their count-table explanation.

    Score and provenance remain unchanged. Every promoted item has already been
    scored by the same retrieval/reranking stage as the other candidates.
    """
    if top_k <= 0:
        return []
    if not CONTEXT_QUERY.search(question) and not PARAMETER_QUERY.search(question):
        return evidence[:top_k]
    selected: list[EvidenceAnchor] = []
    seen = set()
    predicates = []
    if CONTEXT_QUERY.search(question):
        predicates.append(context_statement)
    if PARAMETER_QUERY.search(question):
        predicates.append(parameter_statement)
    for predicate in predicates:
        documents = set()
        # Prefer actual architecture/count column headers to a rounded abstract
        # value. Scores stay unchanged and weak table candidates remain excluded.
        ordered = sorted(evidence, key=lambda item: not bool(PARAMETER_HEADER.search(item.quote))) \
            if predicate is parameter_statement else evidence
        for item in ordered:
            key = item.chunk_id or (item.document_id, item.page_number, item.quote)
            if (item.document_id not in documents and item.score >= min_score
                    and predicate(item)):
                documents.add(item.document_id)
                if key not in seen:
                    selected.append(item)
                    seen.add(key)
                if len(selected) == top_k:
                    return selected
    if PARAMETER_QUERY.search(question):
        # The table alone does not establish which earlier model a baseline is.
        # Keep at most one already-scored same-page equivalence per document.
        pages = {(item.document_id, item.page_number) for item in selected
                 if parameter_statement(item)}
        paired = set()
        for item in evidence:
            key = item.chunk_id or (item.document_id, item.page_number, item.quote)
            if (key not in seen and item.document_id not in paired
                    and (item.document_id, item.page_number) in pages
                    and item.score >= min_score and item.source_type not in {"figure", "reference"}
                    and EQUIVALENCE.search(item.quote)):
                selected.append(item)
                seen.add(key)
                paired.add(item.document_id)
                if len(selected) == top_k:
                    return selected
    selected_keys = {item.chunk_id or (item.document_id, item.page_number, item.quote)
                     for item in selected}
    return (selected + [item for item in evidence if (
        item.chunk_id or (item.document_id, item.page_number, item.quote)
    ) not in selected_keys])[:top_k]
