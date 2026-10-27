import pytest

from app.llm.base import GeneratedSegmentTranslations, LLMResponseError
from app.services.academic_translation import translate_protected_segments
from app.services.evidence_review import _balanced_drafts, _EvidenceDraft


@pytest.mark.asyncio
async def test_token_only_blocks_are_preserved_without_cloud_inference():
    class Provider:
        async def translate_segments(self, segments, source_language, target_language):
            assert [i for i, _ in segments] == ["prose"]
            return GeneratedSegmentTranslations(items={"prose": "示例"})

    segments = [
        ("code", "`making`"),
        ("formula", "$x=y$"),
        ("number", "2017"),
        ("url", "https://example.com/code"),
        ("prose", "Example"),
    ]
    result = await translate_protected_segments(Provider(), segments, "en", "zh")
    assert result.items == {**dict(segments), "prose": "示例"}


@pytest.mark.asyncio
async def test_duplicate_blocks_rejected_before_generation():
    with pytest.raises(LLMResponseError, match="ID 重复"):
        await translate_protected_segments(None, [("same", "One"), ("same", "Two")], "en", "zh")


@pytest.mark.asyncio
async def test_long_segments_are_batched_by_characters_and_restored():
    calls = []

    class Provider:
        async def translate_segments(self, segments, source_language, target_language):
            calls.append(segments)
            return GeneratedSegmentTranslations(items=dict(segments))

    segments = [(str(i), "Text " * 1400 + " $x=y$") for i in range(3)]
    result = await translate_protected_segments(Provider(), segments, "en", "zh")
    assert len(calls) == 3
    assert result.items == dict(segments)


def draft(document_id, kind):
    return _EvidenceDraft(document_id, document_id, 2020, 1, ["b"], None, None, "Excerpt", kind)


def test_twenty_paper_budget_reserves_overview_and_future_coverage():
    candidates = [
        draft(str(i), kind)
        for i in range(20)
        for kind in [
            "overview",
            "overview",
            "future_work",
            "future_work",
            "future_work",
            "future_work",
        ]
    ]
    chosen = _balanced_drafts(candidates, 60)
    assert len(chosen) == 60
    assert {d.document_id for d in chosen[:20]} == {str(i) for i in range(20)}
    assert all(d.kind == "overview" for d in chosen[:20])
    assert all(d.kind == "future_work" for d in chosen[20:40])


def test_small_budget_and_single_paper_order_are_explicit():
    one = [draft("one", kind) for kind in ["overview", "overview", "future_work"]]
    assert _balanced_drafts(one, 2) == one[:2]
    assert len(_balanced_drafts(one + [draft("two", "future_work")], 1)) == 1


def test_acceptance_output_cannot_overwrite_previous_or_production(tmp_path, monkeypatch):
    from scripts import official_functional_inputs
    from scripts.official_functional_inputs import PROJECT, isolated_output

    # Unit tests must not depend on the machine's TMP directory location.
    monkeypatch.setattr(official_functional_inputs, "TEMP_ROOT", tmp_path)

    with pytest.raises(ValueError):
        isolated_output(PROJECT / "backend" / "data" / "forbidden-acceptance")
    output = isolated_output(tmp_path / "new-isolated-run")
    with pytest.raises(ValueError):
        isolated_output(output)
