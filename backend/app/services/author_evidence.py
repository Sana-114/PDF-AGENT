"""Source-backed front-matter evidence, never names inferred from filenames or emails."""

import math
import re
import unicodedata
from typing import Any

AUTHOR_SECTION = "作者与机构 · PDF 排版顺序"
CONTRIBUTOR_SECTION = "贡献分组"
AUTHOR_QUERY = re.compile(r"作者|署名|\bauthors?\b|\bauthor\s+order\b", re.I)
BODY_START = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?(?:abstract\b|introduction\b|methods?\b|"
    r"references?\b|bibliography\b|摘要|引言|参考文献)", re.I,
)
AFFILIATION = re.compile(r"university|institute|research|laborator|google|openai|@", re.I)
AUTHOR_CREDIT = re.compile(r"equal contribution|work performed while|correspondence|作者贡献", re.I)


def _name_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _front_matter_blocks(parsed: dict[str, Any]) -> dict[int, list[dict]]:
    """Separate actual credits from body prose without deleting source blocks.

    A scientific abstract linking to an institution's GitHub is not affiliation
    evidence. Explicit author notes can occur below the abstract in either column.
    """
    result: dict[int, list[dict]] = {}
    in_body = False
    abstract = _name_key(str(parsed.get("abstract") or ""))
    for page in parsed.get("pages", [])[:3]:
        kept = []
        body_tops = []
        for block in page.get("blocks", []):
            text = str(block.get("text") or "")
            normal = _name_key(text)
            box = block.get("bbox")
            if (isinstance(box, list) and len(box) == 4
                    and block.get("type") != "title"
                    and (BODY_START.match(text.strip())
                         or abstract and len(normal) >= 80 and normal in abstract)):
                body_tops.append(float(box[1]))
        boundary = min(body_tops) if body_tops else None
        for block in page.get("blocks", []):
            text = str(block.get("text") or "")
            box = block.get("bbox")
            before_body = not in_body and (boundary is None or (
                isinstance(box, list) and len(box) == 4 and float(box[3]) <= boundary + 2
            ))
            credit_view = re.sub(r"https?://\S+", "", text)
            is_credit = block.get("type") == "footnote" and (
                AUTHOR_CREDIT.search(text)
                or len(text) <= 600 and AFFILIATION.search(credit_view)
                and not re.search(r"\b(?:code|datasets?|models?|train(?:ed|ing)?)\b",
                                  credit_view, re.I)
            )
            if not before_body and not is_credit:
                continue
            # Names may be classified as headings, and a two-column reading
            # order may list them after body prose. Their actual geometry wins.
            kept.append(block)
        result[int(page.get("page_number", 1))] = kept
        in_body = in_body or boundary is not None
    return result


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
    candidates = _front_matter_blocks(parsed)
    for page in parsed.get("pages", [])[:3]:
        blocks = [block for block in candidates.get(int(page.get("page_number", 1)), [])
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
            and AFFILIATION.search(block["text"])
        ) or AUTHOR_CREDIT.search(block["text"])]
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


def validated_contributor_groups(parsed: dict[str, Any]) -> list[dict]:
    """Share the same whole-group source validation across indexing and the reader.

    Do not paste a full cross-page member list onto a one-page evidence anchor.
    Old ASTs without groups remain unchanged. Invalid provenance excludes that
    complete group instead of silently claiming partial membership coverage.
    """
    blocks = {(number, str(block.get("block_id", ""))): block
              for number, page_blocks in _front_matter_blocks(parsed).items()
              for block in page_blocks if block.get("type") != "footnote"}
    verified = []
    groups = parsed.get("contributor_groups", [])
    if not isinstance(groups, list):
        return []
    for group in groups:
        if not isinstance(group, dict):
            continue
        role = group.get("role")
        members = group.get("members")
        sources = group.get("sources")
        if (not isinstance(role, str) or not _name_key(role)
                or not isinstance(members, list) or not members
                or any(not isinstance(name, str) or not name.strip() for name in members)
                or not isinstance(sources, list) or not sources):
            continue
        valid = []
        for source in sources:
            if not isinstance(source, dict):
                break
            number = source.get("page_number")
            block_id = source.get("block_id")
            if (type(number) is not int or not isinstance(block_id, str)
                    or not isinstance(source.get("text"), str) or not source["text"]):
                break
            block = blocks.get((number, block_id))
            box = source.get("bbox")
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                    or box[0] >= box[2] or box[1] >= box[3]
                    or block is None or source.get("text") != block.get("text")
                    or source.get("bbox") != block.get("bbox")):
                break
            valid.append((number, block_id, block))
        if (len(valid) != len(sources)
                or not _name_key(sources[0]["text"]).startswith(_name_key(role))):
            continue
        source_key = _name_key(" ".join(source["text"] for source in sources))
        if any(not _name_key(name) or _name_key(name) not in source_key for name in members):
            continue
        verified.append({
            "role": role, "members": members,
            "sources": [{"page_number": number, "block_id": block_id,
                         "bbox": block["bbox"], "text": block["text"]}
                        for number, block_id, block in valid],
        })
    return verified


def contributor_evidence_nodes(parsed: dict[str, Any]) -> list[dict]:
    """Index page-scoped raw segments, not the complete cross-page member list."""
    nodes = []
    for group in validated_contributor_groups(parsed):
        role = group["role"]
        for source in group["sources"]:
            nodes.append({
                "page_number": source["page_number"], "block_ids": [source["block_id"]],
                "bbox": source["bbox"],
                "section": f"{CONTRIBUTOR_SECTION} · {role}",
                "text": f"Contribution role (structured group): {role}\n"
                "该名单属于贡献分组；不是全局作者顺序。跨页成员须结合各页原文。\n"
                + source["text"],
            })
    return nodes
