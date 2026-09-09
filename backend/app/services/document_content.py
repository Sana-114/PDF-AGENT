"""Read compact structures from the persisted Document AST."""

import json
import re
from typing import Any

from app.services.storage import storage

CITATION_GROUP = re.compile(
    r"\[(\d{1,4}(?:\s*(?:,|;|[-–—])\s*\d{1,4})*)\]"
)
REFERENCE_RANGE = re.compile(r"^(\d{1,4})\s*[-–—]\s*(\d{1,4})$")


def read_parsed_document(document_id: str) -> dict[str, Any] | None:
    path = storage.parsed_path(document_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def outline_items(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    outline = parsed.get("outline")
    if isinstance(outline, list) and outline:
        return outline
    return _legacy_outline(parsed)


def reference_links(
    parsed: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    references = [
        item for item in parsed.get("references", [])
        if isinstance(item, dict) and str(item.get("label", "")).isdigit()
    ]
    reference_labels = {str(item["label"]) for item in references}
    reference_block_ids = {
        str(block_id)
        for item in references
        for block_id in item.get("block_ids", [])
    }
    mentions: list[dict[str, Any]] = []
    for page in parsed.get("pages", []):
        page_number = int(page.get("page_number", 0))
        if page_number < 1:
            continue
        for block in page.get("blocks", []):
            block_id = str(block.get("block_id", ""))
            if not block_id or block_id in reference_block_ids:
                continue
            text = str(block.get("text", ""))
            for match_index, match in enumerate(CITATION_GROUP.finditer(text), start=1):
                for label in _citation_labels(match.group(1)):
                    if label not in reference_labels:
                        continue
                    mentions.append(
                        {
                            "citation_id": (
                                f"cite-{page_number}-{block_id}-{match_index}-{label}"
                            ),
                            "label": label,
                            "page_number": page_number,
                            "block_id": block_id,
                            "bbox": block.get("bbox"),
                            "context": text[:500],
                        }
                    )
    return references, mentions


def _citation_labels(group: str) -> list[str]:
    labels: list[str] = []
    for part in re.split(r"\s*[,;]\s*", group):
        range_match = REFERENCE_RANGE.fullmatch(part)
        if range_match:
            start, end = (int(value) for value in range_match.groups())
            if start <= end and end - start <= 50:
                labels.extend(str(value) for value in range(start, end + 1))
            continue
        if part.strip().isdigit():
            labels.append(str(int(part.strip())))
    return list(dict.fromkeys(labels))


def _legacy_outline(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "text": block["text"],
            "level": block.get("level") or 1,
            "page_number": page["page_number"],
            "block_id": block["block_id"],
            "bbox": block.get("bbox"),
            "children": [],
        }
        for page in parsed.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("type") == "heading"
    ]
