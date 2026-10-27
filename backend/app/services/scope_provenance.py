"""Narrow proposition/source scope checks, not a general entailment verifier.

Values are extracted from actually cited passages, never supplied by paper IDs
or expected answers. Unknown task attribution stays unknown, not guessed.
"""

import re
from decimal import Decimal

from app.schemas.agent import EvidenceAnchor

TASKS = {
    "summarization": re.compile(r"summari[sz]|summaries|\bTL;DR\b|摘要|总结任务", re.I),
    "dialogue": re.compile(r"\bdialogue\b|\bdialog\b|\bAnthropic[- ]HH\b|对话", re.I),
}
NUMBER = r"\d+(?:\.\d+)?"
TEMPERATURE = re.compile(
    r"(?:\b(?:temperatures?|temp)\b|温度)\s*(?:(?:varying\s+from|of|为|是|[:：=])\s*)?"
    rf"(?P<values>{NUMBER}(?:\s*(?:、|,|，|and|to|or|至|到|和|或)\s*{NUMBER})*)",
    re.I,
)


def memory_gain_conflicts(text: str, cited: list[EvidenceAnchor]) -> list[str]:
    """Do not transfer a small accuracy gain onto a memory-saving magnitude.

    Trigger only on actually cited body passages containing both the performance
    context and the ambiguous 'minor gains' wording. No numeric threshold for
    'small memory savings' is invented, and no missing source is auto-selected.
    """
    quotes = [e.quote for e in cited if e.source_type != "reference"]
    performance_context = any(
        re.search(r"\bminor gains?\b", q, re.I)
        and re.search(r"\baccuracy\b|zero[- ]shot|准确率", q, re.I)
        and re.search(r"\bmemory\b|内存|显存", q, re.I) for q in quotes
    )
    direct_memory = (
        r"(?:minor|small|modest|limited)\s+(?:memory|storage)\s+(?:savings?|gains?|reductions?)|"
        r"(?:memory|storage)\s+(?:savings?|gains?|reductions?)\s+"
        r"(?:are|is|were)\s+(?:minor|small|modest|limited)|"
        r"(?:内存|显存)(?:收益|增益|节省|降幅)(?:仅|只|较|很)?(?:小|少|有限)"
    )
    if not performance_context or any(re.search(direct_memory, q, re.I) for q in quotes):
        return []
    for clause in re.split(r"[。;；\n]|(?<!\d)\.(?!\d)", text):
        if re.search(r"并非|不是|不意味着|未报告|不能据此|does not imply|not reported",
                     clause, re.I):
            continue
        if re.search(direct_memory, clause, re.I):
            return ["performance_gain_not_memory_gain"]
    return []


def _temperature_tasks(text: str) -> list[tuple[str, set[str]]]:
    active: set[str] = set()
    facts = []
    # Keep numerical decimal points and the semicolon in TL;DR intact.
    for sentence in re.split(r"[。\n]|(?<!\d)\.(?!\d)|(?<!TL);(?!DR)", text, flags=re.I):
        # Separate explicit task changes, not a list of temperature values.
        parts = re.split(
            r"[,，;；]\s*(?=(?:对话|摘要|(?:for\s+)?(?:summarization|dialogue)))",
            sentence,
            flags=re.I,
        )
        for part in parts:
            tasks = {name for name, pattern in TASKS.items() if pattern.search(part)}
            if tasks:
                active = tasks
            for match in TEMPERATURE.finditer(part):
                for value in re.findall(NUMBER, match["values"]):
                    facts.append((str(Decimal(value).normalize()), set(active)))
    return facts


def sampling_task_conflicts(text: str, cited: list[EvidenceAnchor]) -> list[str]:
    """Detect a temperature explicitly moved to another known task.

    This does not audit other numeric dimensions or infer settings within a
    reported range. A bare number or a temperature with unknown task cannot
    establish a contradiction. Bibliography and unselected evidence never help.
    """
    source: dict[str, list[set[str]]] = {}
    for item in cited:
        if item.source_type == "reference":
            continue
        for value, tasks in _temperature_tasks(item.quote):
            source.setdefault(value, []).append(tasks)
    for value, tasks in _temperature_tasks(text):
        candidates = source.get(value, [])
        if (
            len(tasks) == 1
            and candidates
            and all(len(t) == 1 for t in candidates)
            and not any(tasks & candidate for candidate in candidates)
        ):
            return ["sampling_temperature_task_mismatch"]
    return []


def exclusive_analysis_conflicts(text: str, cited: list[EvidenceAnchor]) -> list[str]:
    """A method/paper is not 'only analysis' when its own excerpt shows more.

    Explicitly limiting the review's discussion or a complexity sub-analysis is
    different from limiting the research itself. This is a bounded contrast.
    """
    exclusivity = re.search(
        r"(?:^|[。;；])\s*(?:[A-Za-z][A-Za-z0-9_-]{2,}|该?(?:论文|研究|工作|方法))"
        r"[^。;；]{0,18}(?<!不)(?<!非)(?:仅(?:做|作|有|是|限于)?|只(?:做|有|是)|限制在|局限于)"
        r"[^。;；]{0,30}(?:理论|复杂度分析|theoretic|complexity analysis)|"
        r"\b(?:paper|study|method|[A-Z][A-Za-z0-9_-]{2,})\s+"
        r"(?:is\s+)?(?:limited\s+to|only\s+(?:provides|presents))\s+"
        r"[^.;]{0,40}(?:analysis|theory)\b",
        text,
        re.I,
    )
    if not exclusivity or re.search(
        r"(?:并非|不是|不只|不仅|not only)\s*$", text[: exclusivity.start()], re.I
    ):
        return []
    for item in cited:
        if item.source_type == "reference":
            continue
        quote = " ".join(item.quote.split())
        algorithm = re.search(
            r"\bwe\s+(?:propose|implement|introduce|develop)\b.{0,100}"
            r"\b(?:algorithm|method|system)\b",
            quote,
            re.I,
        )
        empirical = re.search(
            r"\b(?:experiments?|empirical|we evaluate|we train)\b|"
            r"\btrains?\b.{0,50}\bfaster\b",
            quote,
            re.I,
        )
        if algorithm and empirical:
            return ["paper_not_limited_to_analysis"]
    return []


def restricted_path_length_conflicts(text: str, cited: list[EvidenceAnchor]) -> list[str]:
    reduction = re.search(
        r"(?:限制|局部|邻域)[^。;；]{0,35}(?:降低|缩短|减少)"
        r"(?:最大|长序列|计算|有效|最短|的)*路径(?:长度|长)?|"
        r"\b(?:restrict\w*|local|neighbou?rhood)\b[^.;]{0,70}"
        r"(?:reduc\w*|shorten\w*|decreas\w*)\s+(?:the\s+)?(?:maximum\s+)?path length",
        text,
        re.I,
    )
    if reduction and any(
        item.source_type != "reference"
        and re.search(
            r"(?:increase|lengthen).{0,30}(?:maximum\s+)?path length",
            item.quote,
            re.I,
        )
        and re.search(r"restrict\w*|local|neighbou?rhood", item.quote, re.I)
        for item in cited
    ):
        return ["restricted_attention_path_direction_reversed"]
    return []
