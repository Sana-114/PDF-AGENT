"""Bounded page-by-page document translation orchestration."""

from typing import Any

from app.llm.base import LLMProvider
from app.services.academic_translation import translate_protected_segments
from app.services.document_content import page_text_segments
from app.services.translation_store import TranslationStore, translation_store


def complete_page_segments(parsed: dict[str, Any], page_number: int):
    """Read the entire page; request batching happens later, not by dropping blocks."""
    return page_text_segments(parsed, page_number, max_segments=None, max_characters=None)


def page_translation_matches_source(parsed: dict[str, Any], page_number: int, payload) -> bool:
    if not isinstance(payload, dict) or payload.get("truncated"):
        return False
    if not any(page.get("page_number") == page_number for page in parsed.get("pages", [])):
        return False
    expected, _ = complete_page_segments(parsed, page_number)
    actual = payload.get("segments", [])
    if not isinstance(actual, list) or len(expected) != len(actual):
        return False
    if any(not isinstance(item, dict) for item in actual):
        return False
    preserve_ids = {
        str(block.get("block_id"))
        for page in parsed.get("pages", [])
        if int(page.get("page_number", 0)) == page_number
        for block in page.get("blocks", [])
        if block.get("type") in {"formula", "code"}
    }
    for source, item in zip(expected, actual, strict=True):
        if any(item.get(key) != source[key] for key in ("block_id", "source_text", "bbox")):
            return False
        if not isinstance(item.get("translation"), str) or not item["translation"].strip():
            return False
        if source["block_id"] in preserve_ids and item["translation"] != source["source_text"]:
            return False
    return payload.get("page_number") == page_number


async def translate_page_segments(*, parsed, page_number, provider, target_language):
    segments, truncated = complete_page_segments(parsed, page_number)
    preserve_ids = {
        str(block.get("block_id"))
        for page in parsed.get("pages", [])
        if int(page.get("page_number", 0)) == page_number
        for block in page.get("blocks", [])
        if block.get("type") in {"formula", "code"}
    }
    if len({item["block_id"] for item in segments}) != len(segments):
        from app.llm.base import LLMResponseError

        raise LLMResponseError("待翻译页面存在重复文本块 ID。")
    translatable = [item for item in segments if item["block_id"] not in preserve_ids]
    translated = {
        item["block_id"]: item["source_text"]
        for item in segments
        if item["block_id"] in preserve_ids
    }
    if translatable:
        result = await translate_protected_segments(
            provider,
            [(item["block_id"], item["source_text"]) for item in translatable],
            "auto",
            target_language,
        )
        translated.update(result.items)
    return segments, translated, truncated


def page_translation_payload(
    *,
    document_id: str,
    page_number: int,
    target_language: str,
    provider: str,
    model: str | None,
    segments: list[dict[str, Any]],
    translated: dict[str, str],
    truncated: bool,
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "page_number": page_number,
        "source_language": "auto",
        "target_language": target_language,
        "provider": provider,
        "model": model,
        "truncated": truncated,
        "segments": [{**item, "translation": translated[item["block_id"]]} for item in segments],
    }


async def run_document_translation(
    *,
    document_id: str,
    parsed: dict[str, Any],
    source_fingerprint: str,
    target_language: str,
    provider: LLMProvider,
    store: TranslationStore = translation_store,
    force: bool = False,
) -> dict[str, Any]:
    page_count = max(
        (int(page.get("page_number", 0)) for page in parsed.get("pages", [])),
        default=0,
    )
    manifest = store.prepare(
        document_id=document_id,
        target_language=target_language,
        source_fingerprint=source_fingerprint,
        page_count=page_count,
        provider=provider.name,
        model=provider.model,
        activate=True,
        force=force,
    )
    # Older caches can say completed even when an 80-block page cap dropped
    # appendix labels. Reconcile actual source coverage before trusting status.
    valid_pages = {
        number
        for number in range(1, page_count + 1)
        if page_translation_matches_source(
            parsed,
            number,
            store.read_page(
                document_id, target_language, number, source_fingerprint=source_fingerprint
            ),
        )
    }
    if manifest.get("status") == "completed" and len(valid_pages) == page_count:
        return store.public_status(manifest)
    store.reconcile_completed_pages(document_id, target_language, valid_pages)
    store.set_status(document_id, target_language, "processing")

    page_number = 0
    try:
        for page_number in range(1, page_count + 1):
            if page_number in valid_pages:
                continue
            segments, translated, truncated = await translate_page_segments(
                parsed=parsed,
                page_number=page_number,
                provider=provider,
                target_language=target_language,
            )
            payload = page_translation_payload(
                document_id=document_id,
                page_number=page_number,
                target_language=target_language,
                provider=provider.name,
                model=provider.model,
                segments=segments,
                translated=translated,
                truncated=truncated,
            )
            store.write_page(document_id, target_language, page_number, payload)
        manifest = store.set_status(document_id, target_language, "completed")
        return store.public_status(manifest)
    except Exception as exc:
        try:
            store.record_failure(document_id, target_language, page_number=page_number,
                                 source_fingerprint=source_fingerprint, error=exc)
        except OSError:
            pass  # Preserve the original failure when the disk itself is unavailable.
        store.set_status(document_id, target_language, "failed", error=str(exc)[:2000])
        raise
