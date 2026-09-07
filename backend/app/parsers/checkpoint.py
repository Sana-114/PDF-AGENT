import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = "1.0"


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def read_manifest(checkpoint_dir: Path) -> dict[str, Any] | None:
    path = checkpoint_dir / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def checkpoint_progress(checkpoint_dir: Path) -> dict[str, Any] | None:
    manifest = read_manifest(checkpoint_dir)
    if manifest is None:
        return None
    try:
        completed_pages = max(0, int(manifest.get("completed_pages", 0)))
        page_count = max(0, int(manifest.get("page_count", 0)))
    except (TypeError, ValueError):
        return None
    if page_count:
        completed_pages = min(completed_pages, page_count)
    return {
        "completed_pages": completed_pages,
        "page_count": page_count,
        "percentage": round(completed_pages / page_count * 100, 2) if page_count else 0.0,
        "resumable": completed_pages > 0,
        "updated_at": manifest.get("updated_at"),
    }
