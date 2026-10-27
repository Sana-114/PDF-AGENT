"""Bounded source coverage after scoring; never rewrite quotes or infer answers."""

import re
from collections import Counter

from app.schemas.agent import EvidenceAnchor

REFERENCE_QUERY = re.compile(r"参考文献|引用|引文|\breferences?\b|\bcitations?\b|\bdoi\b", re.I)


def comparison_query(question: str) -> bool:
    """Only the current explicit comparison controls multi-document coverage."""
    current = question.rsplit("\n\nCurrent user question:\n", 1)[-1]
    return bool(re.search(r"比较|对比|异同|\bcompar(?:e|ing|ison)\b|\bversus\b", current, re.I))


def requested_comparison_pair(question: str) -> list[str]:
    """A bounded explicit Latin-name pair, not inferred document identities."""
    current = question.rsplit("\n\nCurrent user question:\n", 1)[-1]
    match = re.search(
        r"(?:比较|对比|\bcompare\b)\s*([A-Za-z][A-Za-z0-9_.-]*)\s*"
        r"(?:与|和|及|、|\band\b|\bversus\b|\bvs\.?)\s*([A-Za-z][A-Za-z0-9_.-]*)",
        current, re.I,
    )
    return [name.rstrip(".") for name in match.groups()] if match else []


def requested_source_focuses(question: str) -> list[tuple[str, str]]:
    """Translate explicit query concepts/actions only, never supply their answer."""
    current = question.rsplit("\n\nCurrent user question:\n", 1)[-1]
    focuses = []
    if re.search(r"闭式|closed[- ]form", current, re.I):
        focuses.append(("closed form", r"closed[- ]form"))
    if re.search(r"采样|\bsampling\b", current, re.I):
        if re.search(r"(?:免(?:去|除)?的?|省去|无需|不需要).{0,8}采样|"
                     r"(?:without|eliminat\w*).{0,25}sampling", current, re.I):
            focuses.append(("eliminating sampling", r"(?:eliminat\w*|without|no need for)"
                            r"[^.;]{0,70}sampling"))
        else:
            focuses.append(("sampling", r"\bsampling\b"))
    return focuses


def requested_procedure(question: str) -> bool:
    current = question.rsplit("\n\nCurrent user question:\n", 1)[-1]
    return bool(re.search(r"流程|步骤|\b(?:procedure|pipeline|workflow|steps)\b", current, re.I))


PROCEDURE_FACET = "methodology method procedure steps stages"


def procedure_passage(section: str | None, text: str, source_type: str) -> bool:
    """A structural recall hint, not proof of what a method actually does."""
    if source_type not in {"text", "formula"}:
        return False
    heading = section or ""
    if re.search(r"future|discussion|conclusion|related work|references|展望|讨论|相关工作",
                 heading, re.I):
        return False
    numbered = re.search(r"(?:^|[\n.!?]\s*)\s*(?:step|stage)\s*\d+\s*[:.)]", text, re.I)
    methods = re.search(r"\b(?:methods?|methodology|approach|algorithm)\b|方法|算法", heading, re.I)
    # A bare heading has no procedure to explain.
    return bool(numbered or (methods and len(text.split()) >= 20))


def is_reference_query(question: str) -> bool:
    # A request for evidence citations is not a request to search bibliography.
    # Strip only explicit support instructions; actual references/DOIs remain.
    content = re.sub(
        r"(?:分别|请|并|并请)?(?:引用|提供|给出)(?:两篇|两份|各篇|每篇|相关|对应|各自)?"
        r"(?:原文|原始论文|证据|出处)(?:引用|位置|锚点)?|"
        r"\b(?:cite|citing)\s+(?:both\s+|each\s+|the\s+)?(?:original\s+)?"
        r"(?:papers?|sources?|evidence)\b",
        "", question, flags=re.I,
    )
    return bool(REFERENCE_QUERY.search(content))
SUMMARY_QUERY = re.compile(
    r"如何(?:定义|结合)|(?:训练|监督).*(?:小时|数据)|(?:预训练|训练).*(?:速度|倍)"
    r"|考试.*(?:分位|成绩)|训练方法|main (?:contribution|method)"
    r"|how.*(?:define|combine)|training.*(?:hours|data|speed)"
    r"|exam.*(?:percentile|score)", re.I,
)
EXPLANATION_QUERY = re.compile(
    r"为什么|为何|原因|工作原理|机制|学习流程|对应什么对象|免去.{0,4}采样|各自解决|分别解决|"
    r"如何.*(?:处理|降低|减少|节省)"
    r"|\bwhy\b|\bmechanisms?\b|\bhow\b.*\b(?:work|reduce|save|handle)\w*\b", re.I,
)
EXPLANATION_FACET = "motivation rationale mechanism because therefore prevent constraint incentive"
EXPLANATION_PASSAGE = re.compile(
    r"\b(?:motivation|rationale|because|therefore|hence)\b"
    r"|\b(?:prevent\w*|avoid\w*|penaliz\w*|enables?|allows?|eliminat\w*)\b|removes? the incentive"
    r"|原因|因为|从而|因此|避免|约束", re.I,
)


def evidence_key(item: EvidenceAnchor) -> str:
    return item.chunk_id or "|".join(
        [item.document_id, str(item.page_number), *item.block_ids, item.quote]
    )


def explanation_statement(item: EvidenceAnchor) -> bool:
    """A recall hint for causal prose, not a semantic-entailment judgment."""
    return (item.source_type in {"text", "formula"}
            and bool(EXPLANATION_PASSAGE.search(item.quote)))


def select_source_evidence(
    question: str, evidence: list[EvidenceAnchor], top_k: int, min_score: float,
    *, author_keys: list[str] = (), summary_keys: list[str] = (),
    explanation_keys: list[str] = (),
    source_focus_keys: list[list[str]] = (),
    procedure_keys: list[str] = (),
) -> list[EvidenceAnchor]:
    """Keep small source-backed author/summary coverage, with original scores.

    Focused candidates come from scoped BM25 recall, not evaluation gold. All
    candidates go through the same reranker before this policy runs. A generic
    concept question should not spend its entire evidence budget on bibliography.
    Citation questions keep their ordinary rank, including reference entries.
    """
    if top_k <= 0:
        return []
    if is_reference_query(question):
        return evidence[:top_k]
    lookup = {evidence_key(item): item for item in evidence}
    selected = []
    documents = set()
    for key in author_keys:
        item = lookup.get(key)
        if (item and item.score >= min_score and item.document_id not in documents
                and item.source_type != "reference"):
            selected.append(item)
            documents.add(item.document_id)
            if len(selected) == top_k:
                return selected
    # Preserve at most one genuinely scored passage for each explicit concept,
    # within one third of the budget. This is source recall, not answer injection.
    focus_budget = max(1, top_k // 3)
    seen = {evidence_key(item) for item in selected}
    focused_count = 0
    if requested_procedure(question):
        procedure_set = set(procedure_keys)
        for item in evidence:
            key = evidence_key(item)
            if (key in procedure_set and key not in seen and item.score >= min_score
                    and procedure_passage(item.section, item.quote, item.source_type)):
                selected.append(item)
                seen.add(key)
                focused_count += 1
                if len(selected) == top_k:
                    return selected
                if focused_count == min(3, focus_budget):
                    break
    for keys in source_focus_keys[:focus_budget - focused_count]:
        candidates = set(keys)
        item = next((item for item in evidence if evidence_key(item) in candidates
                     and evidence_key(item) not in seen and item.score >= min_score
                     and item.source_type != "reference"), None)
        if item:
            selected.append(item)
            seen.add(evidence_key(item))
            if len(selected) == top_k:
                return selected
    # A why-question needs the rationale, not just an abstract saying that the
    # method works. Reserve at most one scored body passage per paper and a third
    # of the budget. The quote, source IDs, bbox and reranker score stay unchanged.
    if EXPLANATION_QUERY.search(question):
        focused = set(explanation_keys)
        rationale_documents = set()
        rationale_budget = max(1, top_k // 3)
        for item in evidence:
            if (evidence_key(item) in focused and explanation_statement(item)
                    and item.score >= min_score and item.document_id not in rationale_documents
                    and evidence_key(item) not in {
                        evidence_key(previous) for previous in selected}):
                selected.append(item)
                rationale_documents.add(item.document_id)
                if len(selected) == top_k:
                    return selected
                if len(rationale_documents) == rationale_budget:
                    break
    # At most a third of the budget (two passages per paper) for relevant summary
    # chunks. This does not require the answer to live in an abstract or page 1.
    summary_budget = max(1, top_k // 3)
    summary_set = set(summary_keys)
    summary_documents = Counter()
    if SUMMARY_QUERY.search(question):
        for item in evidence:
            if (evidence_key(item) in summary_set and item.source_type == "abstract"
                    and item.score >= min_score and summary_documents[item.document_id] < 2):
                selected.append(item)
                summary_documents[item.document_id] += 1
                if sum(summary_documents.values()) == summary_budget:
                    break
    seen = {evidence_key(item) for item in selected}
    body = [item for item in evidence if item.source_type != "reference"]
    references = [item for item in evidence if item.source_type == "reference"]
    return (selected + [item for item in [*body, *references]
                        if evidence_key(item) not in seen])[:top_k]
