import pytest

from app.llm.base import GeneratedSegmentTranslations
from app.services.document_translation import run_document_translation
from app.services.translation_store import TranslationStore


def _parsed_document() -> dict:
    return {
        "pages": [
            {
                "page_number": 1,
                "blocks": [{"block_id": "p1-b1", "text": "First page", "bbox": [1, 2, 3, 4]}],
            },
            {
                "page_number": 2,
                "blocks": [{"block_id": "p2-b1", "text": "Second page", "bbox": [5, 6, 7, 8]}],
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
    failures = list(store.job_dir("doc-1", "zh").glob("failure-*.json"))
    assert len(failures) == 1
    import json
    diagnostic = json.loads(failures[0].read_text(encoding="utf-8"))
    assert diagnostic["page_number"] == 2 and diagnostic["source_fingerprint"] == "source-v1"

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
    assert failures[0].exists()  # Resuming preserves the original failed attempt.
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


@pytest.mark.asyncio
async def test_whole_translation_keeps_all_110_blocks_and_typed_formulas(tmp_path) -> None:
    blocks = [{"block_id": f"b-{i}", "text": f"Label {i}", "type": "text"} for i in range(110)]
    blocks[45].update(type="formula", text="Attention(Q, K, V) = softmax(QKT / sqrt(dk))V")
    blocks[46].update(type="code", text="return x + y")
    provider = RecordingProvider()
    store = TranslationStore(tmp_path / "translations")
    result = await run_document_translation(
        document_id="large-page",
        parsed={"pages": [{"page_number": 1, "blocks": blocks}]},
        source_fingerprint="test",
        target_language="zh",
        provider=provider,
        store=store,
    )
    assert result["status"] == "completed"
    assert [len(call) for call in provider.calls] == [40, 40, 28]
    cached = store.read_page("large-page", "zh", 1)
    assert cached and not cached["truncated"]
    assert [item["block_id"] for item in cached["segments"]] == [b["block_id"] for b in blocks]
    assert cached["segments"][45]["translation"] == blocks[45]["text"]
    assert cached["segments"][46]["translation"] == blocks[46]["text"]


@pytest.mark.asyncio
async def test_later_batch_failure_does_not_commit_partial_page(tmp_path) -> None:
    blocks = [{"block_id": f"b-{i}", "text": "Text"} for i in range(85)]
    store = TranslationStore(tmp_path / "translations")
    provider = RecordingProvider(fail_on="b-80")
    with pytest.raises(RuntimeError):
        await run_document_translation(
            document_id="incomplete",
            parsed={"pages": [{"page_number": 1, "blocks": blocks}]},
            source_fingerprint="test",
            target_language="zh",
            provider=provider,
            store=store,
        )
    assert store.read_page("incomplete", "zh", 1) is None
    assert store.read_manifest("incomplete", "zh")["status"] == "failed"


@pytest.mark.asyncio
async def test_old_completed_truncated_cache_is_repaired_not_trusted(tmp_path):
    from app.services.document_translation import page_translation_payload

    parsed = _parsed_document()
    store = TranslationStore(tmp_path / "translations")
    store.prepare(
        document_id="legacy",
        target_language="zh",
        source_fingerprint="v1",
        page_count=3,
        provider="recording",
        model="translator-v1",
        activate=True,
    )
    store.write_page(
        "legacy",
        "zh",
        1,
        page_translation_payload(
            document_id="legacy",
            page_number=1,
            target_language="zh",
            provider="recording",
            model="translator-v1",
            segments=[],
            translated={},
            truncated=True,
        ),
    )
    store.set_status("legacy", "zh", "completed")
    provider = RecordingProvider()
    result = await run_document_translation(
        document_id="legacy",
        parsed=parsed,
        source_fingerprint="v1",
        target_language="zh",
        provider=provider,
        store=store,
    )
    assert result["status"] == "completed" and result["completed_pages"] == 3
    assert provider.calls == [["p1-b1"], ["p2-b1"]]
    assert not store.read_page("legacy", "zh", 1)["truncated"]


def test_cache_validation_rejects_wrong_source_formula_or_phantom_page():
    from app.services.document_translation import page_translation_matches_source

    parsed = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {"block_id": "b1", "text": "x=y", "type": "formula", "bbox": [1, 2, 3, 4]}
                ],
            }
        ]
    }
    segment = {"block_id": "b1", "source_text": "x=y", "translation": "x=y", "bbox": [1, 2, 3, 4]}
    payload = {"page_number": 1, "segments": [segment], "truncated": False}
    assert page_translation_matches_source(parsed, 1, payload)
    segment["translation"] = "changed"
    assert not page_translation_matches_source(parsed, 1, payload)
    assert not page_translation_matches_source(parsed, 2, {"page_number": 2, "segments": []})
