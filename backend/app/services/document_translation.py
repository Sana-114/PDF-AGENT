"""Bounded page-by-page document translation orchestration."""

from typing import Any

from app.llm.base import LLMProvider
from app.services.document_content import page_text_segments
from app.services.translation_store import TranslationStore, translation_store


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
        "segments": [
            {**item, "translation": translated[item["block_id"]]} for item in segments
        ],
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
    if manifest.get("status") == "completed":
        return store.public_status(manifest)
    store.set_status(document_id, target_language, "processing")

    try:
        for page_number in range(1, page_count + 1):
            if store.read_page(
                document_id,
                target_language,
                page_number,
                source_fingerprint=source_fingerprint,
            ):
                continue
            segments, truncated = page_text_segments(parsed, page_number)
            if segments:
                result = await provider.translate_segments(
                    [(item["block_id"], item["source_text"]) for item in segments],
                    "auto",
                    target_language,
                )
                translated = result.items
            else:
                translated = {}
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
        store.set_status(document_id, target_language, "failed", error=str(exc)[:2000])
        raise
