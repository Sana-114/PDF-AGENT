"""Source-backed front-matter evidence, never names inferred from filenames or emails."""

import re
from typing import Any

AUTHOR_SECTION = "作者与机构 · PDF 排版顺序"
AUTHOR_QUERY = re.compile(r"作者|\bauthors?\b|\bauthor\s+order\b", re.I)


def _name_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def author_evidence_nodes(parsed: dict[str, Any]) -> list[dict]:
    """Emit ordinal metadata only if every parsed name has a front-page text anchor.

    The parser supplies the geometric author order. Include original source blocks
    so affiliations/footnotes are not inferred from an unordered institution list.
    A missing name invalidates the whole ordered list rather than shifting ordinals.
    """
    authors = parsed.get("authors", [])
    if not authors or any(not isinstance(name, str) or not name.strip() for name in authors):
        return []
    keys = [_name_key(name) for name in authors]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        return []
    for page in parsed.get("pages", [])[:3]:
        blocks = [block for block in page.get("blocks", [])
                  if block.get("block_id") and isinstance(block.get("text"), str)
                  and isinstance(block.get("bbox"), list) and len(block["bbox"]) == 4]
        matched = [block for block in blocks
                   if any(key in _name_key(block["text"]) for key in keys)]
        if not all(any(key in _name_key(block["text"]) for block in matched) for key in keys):
            continue
        # Preserve nearby affiliations and explanatory author footnotes verbatim.
        top, bottom = min(b["bbox"][1] for b in matched), max(b["bbox"][3] for b in matched)
        included = [block for block in blocks if block in matched or (
            top <= block["bbox"][1] <= bottom + 60
            and re.search(
                r"university|institute|research|laborator|google|openai|@", block["text"], re.I
            )
        ) or re.search(r"equal contribution|work performed while|作者贡献", block["text"], re.I)]
        number = int(page.get("page_number", 1))
        return [{
            "page_number": number,
            "block_ids": [f"p{number}-metadata-authors", *[b["block_id"] for b in included]],
            "bbox": [min(b["bbox"][0] for b in included), min(b["bbox"][1] for b in included),
                     max(b["bbox"][2] for b in included), max(b["bbox"][3] for b in included)],
            "section": AUTHOR_SECTION,
            "text": "Authors / author order / affiliations / institutions\n"
            "以下序号来自 PDF 作者排版顺序，属于结构化解析结果，不是原文编号：\n"
            + "\n".join(f"{index}. {name}" for index, name in enumerate(authors, 1))
            + "\nOriginal front-matter source blocks (affiliations and footnotes):\n"
            + "\n".join(b["text"] for b in included),
        }]
    return []
