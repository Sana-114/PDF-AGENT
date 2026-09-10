"""Persistent, resumable storage for aligned document translations."""

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from app.core.config import settings
from app.parsers.checkpoint import utc_timestamp, write_json_atomic

TRANSLATION_SCHEMA_VERSION = "1.0"
TranslationStatus = Literal["partial", "queued", "processing", "completed", "failed"]


def _page_numbers(values: Any) -> set[int]:
    if not isinstance(values, list):
        return set()
    pages: set[int] = set()
    for value in values:
        try:
            page_number = int(value)
        except (TypeError, ValueError):
            continue
        if page_number > 0:
            pages.add(page_number)
    return pages


class TranslationStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.translation_dir).resolve()

    def job_dir(self, document_id: str, target_language: str) -> Path:
        if not document_id or any(value in document_id for value in ("/", "\\", "..")):
            raise ValueError("Invalid document id")
        if target_language not in {"zh", "en"}:
            raise ValueError("Invalid target language")
        document_dir = (self.root / document_id).resolve()
        if document_dir.parent != self.root:
            raise ValueError("Invalid document id")
        job_dir = (document_dir / target_language).resolve()
        if job_dir.parent != document_dir:
            raise ValueError("Invalid target language")
        return job_dir

    def read_manifest(self, document_id: str, target_language: str) -> dict[str, Any] | None:
        path = self.job_dir(document_id, target_language) / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != TRANSLATION_SCHEMA_VERSION
        ):
            return None
        return payload

    def prepare(
        self,
        *,
        document_id: str,
        target_language: str,
        source_fingerprint: str,
        page_count: int,
        provider: str,
        model: str | None,
        activate: bool,
        force: bool = False,
    ) -> dict[str, Any]:
        job_dir = self.job_dir(document_id, target_language)
        manifest = self.read_manifest(document_id, target_language)
        try:
            stored_page_count = int(manifest.get("page_count", 0)) if manifest else 0
        except (TypeError, ValueError):
            stored_page_count = 0
        compatible = bool(
            manifest
            and manifest.get("document_id") == document_id
            and manifest.get("target_language") == target_language
            and manifest.get("source_fingerprint") == source_fingerprint
            and stored_page_count == page_count
            and manifest.get("provider") == provider
            and manifest.get("model") == model
        )
        if force or not compatible:
            if job_dir.exists():
                shutil.rmtree(job_dir)
            manifest = None
        if manifest is None:
            manifest = {
                "schema_version": TRANSLATION_SCHEMA_VERSION,
                "document_id": document_id,
                "source_fingerprint": source_fingerprint,
                "source_language": "auto",
                "target_language": target_language,
                "provider": provider,
                "model": model,
                "status": "queued" if activate else "partial",
                "page_count": page_count,
                "completed_pages": [],
                "error": None,
                "updated_at": utc_timestamp(),
            }
        elif activate and manifest.get("status") != "completed":
            manifest["status"] = "queued"
            manifest["error"] = None
            manifest["updated_at"] = utc_timestamp()
        write_json_atomic(job_dir / "manifest.json", manifest)
        return manifest

    def read_page(
        self,
        document_id: str,
        target_language: str,
        page_number: int,
        *,
        source_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        if page_number < 1:
            return None
        manifest = self.read_manifest(document_id, target_language)
        if manifest is None:
            return None
        if source_fingerprint and manifest.get("source_fingerprint") != source_fingerprint:
            return None
        path = self.job_dir(document_id, target_language) / f"page-{page_number:06d}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def write_page(
        self,
        document_id: str,
        target_language: str,
        page_number: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_dir = self.job_dir(document_id, target_language)
        manifest = self.read_manifest(document_id, target_language)
        if manifest is None:
            raise RuntimeError("Translation manifest is missing")
        write_json_atomic(job_dir / f"page-{page_number:06d}.json", payload)
        completed = _page_numbers(manifest.get("completed_pages"))
        completed.add(page_number)
        manifest["completed_pages"] = sorted(completed)
        manifest["updated_at"] = utc_timestamp()
        write_json_atomic(job_dir / "manifest.json", manifest)
        return manifest

    def set_status(
        self,
        document_id: str,
        target_language: str,
        status: TranslationStatus,
        *,
        error: str | None = None,
    ) -> dict[str, Any]:
        job_dir = self.job_dir(document_id, target_language)
        manifest = self.read_manifest(document_id, target_language)
        if manifest is None:
            raise RuntimeError("Translation manifest is missing")
        manifest["status"] = status
        manifest["error"] = error
        manifest["updated_at"] = utc_timestamp()
        write_json_atomic(job_dir / "manifest.json", manifest)
        return manifest

    @staticmethod
    def public_status(manifest: dict[str, Any]) -> dict[str, Any]:
        try:
            page_count = max(0, int(manifest.get("page_count", 0)))
        except (TypeError, ValueError):
            page_count = 0
        completed_pages = len(_page_numbers(manifest.get("completed_pages")))
        if page_count:
            completed_pages = min(completed_pages, page_count)
        return {
            "document_id": str(manifest.get("document_id", "")),
            "source_language": "auto",
            "target_language": str(manifest.get("target_language", "")),
            "provider": str(manifest.get("provider", "")),
            "model": manifest.get("model"),
            "status": str(manifest.get("status", "partial")),
            "page_count": page_count,
            "completed_pages": completed_pages,
            "percentage": round(completed_pages / page_count * 100, 2) if page_count else 0.0,
            "resumable": completed_pages > 0 and completed_pages < page_count,
            "error": manifest.get("error"),
            "updated_at": manifest.get("updated_at"),
        }


translation_store = TranslationStore()
