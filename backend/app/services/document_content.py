"""Read compact structures from the persisted Document AST."""

import json
from typing import Any

from app.services.storage import storage


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
