import pytest

from app.llm.base import GeneratedSegmentTranslations
from app.services.document_translation import run_document_translation
from app.services.translation_store import TranslationStore


def _parsed_document() -> dict:
    return {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {"block_id": "p1-b1", "text": "First page", "bbox": [1, 2, 3, 4]}
                ],
            },
            {
                "page_number": 2,
                "blocks": [
                    {"block_id": "p2-b1", "text": "Second page", "bbox": [5, 6, 7, 8]}
                ],
            },
            {"page_number": 3, "blocks": []},
        ]
    }


class RecordingProvider:
    name = "recording"
    model = "translator-v1"
    supports_translation = True

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[list[str]] = []

    async def translate_segments(
        self,
        segments: list[tuple[str, str]],
        source_language: str,
        target_language: str,
    ) -> GeneratedSegmentTranslations:
        del source_language, target_language
        block_ids = [block_id for block_id, _ in segments]
        self.calls.append(block_ids)
        if self.fail_on in block_ids:
            raise RuntimeError("temporary provider failure")
        return GeneratedSegmentTranslations(
            items={block_id: f"translated:{text}" for block_id, text in segments}
        )


@pytest.mark.asyncio
async def test_document_translation_resumes_from_persisted_pages(tmp_path) -> None:
    store = TranslationStore(tmp_path / "translations")
    first_provider = RecordingProvider(fail_on="p2-b1")

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        await run_document_translation(
            document_id="doc-1",
            parsed=_parsed_document(),
            source_fingerprint="source-v1",
            target_language="zh",
            provider=first_provider,
            store=store,
        )

    failed = store.public_status(store.read_manifest("doc-1", "zh") or {})
    assert failed["status"] == "failed"
    assert failed["completed_pages"] == 1
    assert failed["resumable"] is True
    assert first_provider.calls == [["p1-b1"], ["p2-b1"]]

    resumed_provider = RecordingProvider()
    completed = await run_document_translation(
        document_id="doc-1",
        parsed=_parsed_document(),
        source_fingerprint="source-v1",
        target_language="zh",
        provider=resumed_provider,
        store=store,
    )

    assert completed["status"] == "completed"
    assert completed["completed_pages"] == 3
    assert completed["percentage"] == 100.0
    assert resumed_provider.calls == [["p2-b1"]]
    page = store.read_page("doc-1", "zh", 2, source_fingerprint="source-v1")
    assert page and page["segments"][0]["block_id"] == "p2-b1"


def test_translation_store_invalidates_cache_for_new_source_or_model(tmp_path) -> None:
    store = TranslationStore(tmp_path / "translations")
    store.prepare(
        document_id="doc-2",
        target_language="en",
        source_fingerprint="source-v1",
        page_count=2,
        provider="provider",
        model="model-v1",
        activate=False,
    )
    store.write_page("doc-2", "en", 1, {"page_number": 1, "segments": []})

    manifest = store.prepare(
        document_id="doc-2",
        target_language="en",
        source_fingerprint="source-v2",
        page_count=2,
        provider="provider",
        model="model-v2",
        activate=True,
    )

    assert manifest["completed_pages"] == []
    assert manifest["status"] == "queued"
    assert store.read_page("doc-2", "en", 1) is None
