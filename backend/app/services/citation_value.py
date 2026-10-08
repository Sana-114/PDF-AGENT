"""Conservative citation-use screening, independent of bibliographic identity.

These rules identify explicit uses in the citing sentence; they do not claim to
measure scientific quality or infer author intent. Uncertain references remain
available for manual review and no bibliography entries are deleted.
"""

import re
from typing import Any

from app.schemas.discovery import CitationContextRead, CitationValueRead
from app.services.document_content import reference_links

METHOD = re.compile(
    r"\bwe\s+(?:\w+\s+){0,2}(?:adopt|use|follow|extend|build|implement)\b|"
    r"\b(?:based on|building on|following the|implementation of)\b|"
    r"(?:本文|我们)(?:采用|使用|沿用|扩展)|基于.{0,25}(?:方法|模型)",
    re.I,
)
BASELINE = re.compile(
    r"\b(?:baseline|compared?\s+(?:to|with|against)|outperform|benchmark against)\b|"
    r"(?:作为基线|对比实验|比较对象|优于)",
    re.I,
)
BACKGROUND = re.compile(
    r"\b(?:previous work|prior work|related work|proposed|introduced|has been studied)\b|"
    r"(?:相关工作|已有研究|先前研究|提出了)",
    re.I,
)
INCIDENTAL = re.compile(
    r"\b(?:for an? (?:overview|survey)|see also|for completeness|among others)\b|"
    r"(?:综述可参见|另见|为完整性)",
    re.I,
)
NEGATED_USE = re.compile(
    r"\b(?:not|never|without|do not|don't)\b.{0,35}\b(?:adopt|use|follow|extend|compare)\b|"
    r"(?:未|不)(?:采用|使用|沿用|比较)",
    re.I,
)
REASONS = {
    "method": "引用句包含方法采用或扩展线索，建议优先阅读；仍需核对具体贡献。",
    "baseline": "引用句包含实验基线或性能比较线索，建议优先阅读。",
    "background": "目前只识别到背景介绍，未默认勾选；可手动纳入。",
    "incidental": "仅识别到泛指或延伸阅读语境，推荐模式暂不下钻；不等于低质量论文。",
    "unknown": "正文证据不足或作用不明确，不自动判断引用价值。",
}


def _sentence(text: str, start: int, end: int) -> str:
    # Restrict classification to this occurrence, never a different sentence
    # earlier in the same PDF block. Preserve the literal quote for verification.
    boundaries = [m.end() for m in re.finditer(r"[.!?。！？](?:\s+|$)", text)]
    left = max((offset for offset in boundaries if offset <= start), default=0)
    right = min((offset for offset in boundaries if offset >= end), default=len(text))
    return text[left:right].strip()


def _role(quote: str) -> str:
    if NEGATED_USE.search(quote):
        return "unknown"
    if BASELINE.search(quote):
        return "baseline"
    if METHOD.search(quote):
        return "method"
    if BACKGROUND.search(quote):
        return "background"
    if INCIDENTAL.search(quote):
        return "incidental"
    return "unknown"


def citation_values(parsed: dict[str, Any]) -> dict[str, CitationValueRead]:
    references, mentions = reference_links(parsed)
    blocks = {
        (int(page.get("page_number", 0)), str(block.get("block_id", ""))): block
        for page in parsed.get("pages", [])
        for block in page.get("blocks", [])
    }
    by_label: dict[str, list[CitationContextRead]] = {}
    for mention in mentions:
        block = blocks.get((mention["page_number"], mention["block_id"]), {})
        text = str(block.get("text", ""))
        quote = _sentence(text, mention["start_offset"], mention["end_offset"])
        # Very large run-on OCR text is not safe to attribute to one reference.
        role = _role(quote) if len(quote) <= 1600 else "unknown"
        by_label.setdefault(mention["label"], []).append(
            CitationContextRead(
                citation_id=mention["citation_id"],
                page_number=mention["page_number"],
                block_id=mention["block_id"],
                bbox=mention.get("bbox"),
                quote=quote[:1600],
                role=role,
            )
        )
    results: dict[str, CitationValueRead] = {}
    for reference in references:
        contexts = by_label.get(str(reference["label"]), [])
        roles = {context.role for context in contexts}
        role = next(
            (item for item in ("method", "baseline", "background") if item in roles),
            "incidental" if roles == {"incidental"} else "unknown",
        )
        # Keep substantive contexts first within the bounded response payload.
        ordered = sorted(contexts, key=lambda item: item.role not in {"method", "baseline"})
        results[str(reference.get("reference_id", ""))] = CitationValueRead(
            role=role,
            recommended=role in {"method", "baseline"},
            reason=REASONS[role],
            mention_count=len(contexts),
            contexts=ordered[:5],
        )
    return results
