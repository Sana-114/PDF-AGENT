"""Deterministic, evidence-linked comparison for two versions of one paper."""

from dataclasses import dataclass
from typing import Any

from app.services.fingerprints import bottom_k_signature, normalize_text, signature_similarity

STRUCTURE_KEYS = ("tables", "figures", "formulas", "references", "appendices")


@dataclass(slots=True, frozen=True)
class _Passage:
    page_number: int
    block_id: str
    text: str
    normalized: str


def _document_text(parsed: dict[str, Any]) -> str:
    return "\n".join(
        str(block.get("text", "")).strip()
        for page in parsed.get("pages", [])
        for block in page.get("blocks", [])
        if str(block.get("text", "")).strip()
    )


def _flatten_outline(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        text = str(value.get("text", "")).strip()
        if text:
            result.append(
                {
                    "text": text,
                    "normalized": normalize_text(text),
                    "page_number": int(value.get("page_number", 0)),
                    "level": int(value.get("level", 1)),
                }
            )
        result.extend(_flatten_outline(value.get("children")))
    return result


def _passages(parsed: dict[str, Any]) -> list[_Passage]:
    values: list[_Passage] = []
    for page in parsed.get("pages", []):
        page_number = int(page.get("page_number", 0))
        for block in page.get("blocks", []):
            if block.get("type") not in {"text", "formula"}:
                continue
            text = str(block.get("text", "")).strip()
            normalized = normalize_text(text)
            if len(normalized) < 80:
                continue
            values.append(
                _Passage(
                    page_number=page_number,
                    block_id=str(block.get("block_id", "")),
                    text=text,
                    normalized=normalized,
                )
            )
    return values


def _distinct_passages(
    candidates: list[_Passage],
    other_text: str,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    other_tokens = set(normalize_text(other_text).split())
    scored: list[tuple[float, _Passage]] = []
    seen: set[str] = set()
    for passage in candidates:
        if passage.normalized in seen:
            continue
        seen.add(passage.normalized)
        tokens = set(passage.normalized.split())
        if not tokens:
            continue
        coverage = len(tokens & other_tokens) / len(tokens)
        if coverage > 0.62:
            continue
        distinctiveness = 1 - coverage
        score = distinctiveness * min(len(passage.normalized), 800)
        scored.append((score, passage))
    scored.sort(key=lambda item: (-item[0], item[1].page_number, item[1].block_id))
    return [
        {
            "page_number": passage.page_number,
            "block_id": passage.block_id,
            "text": passage.text[:800],
        }
        for _, passage in scored[:limit]
    ]


def _version_label(document: Any) -> str:
    version = getattr(document, "arxiv_version", None)
    return f"v{version}" if version else getattr(document, "original_filename", "未知版本")


def compare_document_versions(
    *,
    current: Any,
    existing: Any,
    current_parsed: dict[str, Any],
    existing_parsed: dict[str, Any],
) -> dict[str, Any]:
    """Compare persisted ASTs and return conservative differences with source anchors."""
    current_text = _document_text(current_parsed)
    existing_text = _document_text(existing_parsed)
    overlap = signature_similarity(
        bottom_k_signature(current_text),
        bottom_k_signature(existing_text),
    )

    current_outline = _flatten_outline(current_parsed.get("outline"))
    existing_outline = _flatten_outline(existing_parsed.get("outline"))
    current_heading_keys = {item["normalized"] for item in current_outline}
    existing_heading_keys = {item["normalized"] for item in existing_outline}
    added_headings = [
        {key: item[key] for key in ("text", "page_number", "level")}
        for item in current_outline
        if item["normalized"] not in existing_heading_keys
    ][:20]
    removed_headings = [
        {key: item[key] for key in ("text", "page_number", "level")}
        for item in existing_outline
        if item["normalized"] not in current_heading_keys
    ][:20]

    structure_deltas = {
        key: len(current_parsed.get(key, [])) - len(existing_parsed.get(key, []))
        for key in STRUCTURE_KEYS
    }
    current_pages = int(getattr(current, "page_count", 0) or len(current_parsed.get("pages", [])))
    existing_pages = int(
        getattr(existing, "page_count", 0) or len(existing_parsed.get("pages", []))
    )
    page_delta = current_pages - existing_pages
    text_delta = len(current_text) - len(existing_text)

    summary = [
        f"正在比较当前 {_version_label(current)} 与库中 {_version_label(existing)}。",
        f"正文指纹重合度约为 {overlap:.1%}；该数值用于版本修订提示，不代表语义等价。",
        f"当前版本页数变化 {page_delta:+d} 页，抽取文本字符变化 {text_delta:+d}。",
    ]
    if added_headings or removed_headings:
        summary.append(
            f"检测到 {len(added_headings)} 个当前版本新增标题、"
            f"{len(removed_headings)} 个当前版本不再出现的标题。"
        )
    changed_structures = [
        f"{key} {delta:+d}" for key, delta in structure_deltas.items() if delta
    ]
    if changed_structures:
        summary.append("结构节点数量变化：" + "，".join(changed_structures) + "。")

    return {
        "current_document_id": current.id,
        "existing_document_id": existing.id,
        "current_label": _version_label(current),
        "existing_label": _version_label(existing),
        "content_overlap": round(overlap, 4),
        "page_delta": page_delta,
        "text_character_delta": text_delta,
        "structure_deltas": structure_deltas,
        "added_headings": added_headings,
        "removed_headings": removed_headings,
        "possibly_added_passages": _distinct_passages(
            _passages(current_parsed), existing_text
        ),
        "possibly_removed_passages": _distinct_passages(
            _passages(existing_parsed), current_text
        ),
        "summary": summary,
    }
