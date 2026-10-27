import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import LLMResponseError
from app.models.document import Document, DocumentStatus
from app.models.future_work import FutureWorkAssessment
from app.schemas.agent import EvidenceAnchor
from app.schemas.review import FutureWorkAssessmentRequest, FutureWorkSource
from app.services.future_progress import assess_progress, read_assessment, validate_assessment
from app.services.future_work_review import direction_key

GOAL = "Future work should extend retrieval to images, audio, and video."
PROGRESS = "We present a retrieval method for images and evaluate image retrieval experiments."


def raw(goal=GOAL, status="partial", quote=PROGRESS):
    return {
        "facets": [
            {
                "goal_quote": goal,
                "status": status,
                "explanation": "后续文中展示图像检索实验，不据此确认音频和视频目标已实现。",
                "proof": [{"evidence_id": "P1", "quote": quote}]
                if status != "not_established"
                else [],
            }
        ]
    }


def anchors():
    return (
        EvidenceAnchor(evidence_id="S0", document_id="old", page_number=1, quote=GOAL, score=1),
        [
            EvidenceAnchor(
                evidence_id="P1", document_id="new", page_number=1, quote=PROGRESS, score=1
            )
        ],
    )


@pytest.mark.parametrize(
    "change",
    [
        "unknown_id",
        "wrong_quote",
        "wrong_goal",
        "missing_proof",
        "wrong_status_type",
        "duplicate_proof",
    ],
)
def test_invalid_assessment_is_not_accepted(change):
    data = raw()
    item = data["facets"][0]
    if change == "unknown_id":
        item["proof"][0]["evidence_id"] = "P999"
    elif change == "wrong_quote":
        item["proof"][0]["quote"] = "We solve every possible multimodal task."
    elif change == "wrong_goal":
        item["goal_quote"] = "Future work is already solved."
    elif change == "missing_proof":
        item["proof"] = []
    elif change == "wrong_status_type":
        item["status"] = []
    else:
        item["proof"] *= 2
    with pytest.raises(LLMResponseError):
        validate_assessment(data, *anchors())


def test_partial_goal_does_not_become_whole_goal_resolution():
    status, facets, coverage, warnings = validate_assessment(raw(), *anchors())
    assert status == "partial_progress" and coverage
    assert facets[0].status == "partial" and facets[0].proof[0].quote == PROGRESS
    assert any("不能证明" in warning for warning in warnings)
    data = raw(goal="extend retrieval to images", status="addressed")
    status, _, coverage, _ = validate_assessment(data, *anchors())
    assert status == "partial_progress" and not coverage


def test_distinct_spans_from_one_paragraph_preserve_both_proofs_and_numeric_support():
    source, progress = anchors()
    first, second = "The image evaluation uses 13 cases.", "The audio evaluation uses 29 cases."
    progress[0] = progress[0].model_copy(update={"quote": first + " " + second})
    data = raw(quote=first)
    item = data["facets"][0]
    item["explanation"] = "后续实验使用 13 和 29 个样本；原计划的完整范围尚未核验。"
    item["proof"].append({"evidence_id": "P1", "quote": second})
    status, facets, _, _ = validate_assessment(data, source, progress)
    assert status == "partial_progress"
    assert facets[0].evidence_ids == ["P1"]
    assert [p.quote for p in facets[0].proof] == [first, second]
    assert all(p.quote in progress[0].quote for p in facets[0].proof)
    item["proof"][1]["quote"] = "The audio evaluation uses 999 cases."
    with pytest.raises(LLMResponseError):
        validate_assessment(data, source, progress)


def test_even_all_addressed_is_progress_not_automatic_resolution():
    status, _, coverage, _ = validate_assessment(raw(status="addressed"), *anchors())
    assert status == "supported_progress" and coverage
    assert "resolved" not in status


def test_not_established_does_not_publish_uncited_global_speculation():
    data = raw(status="not_established")
    data["facets"][0]["explanation"] = "这个问题全球从来没有人解决，而且永久无法解决。"
    status, facets, _, _ = validate_assessment(data, *anchors())
    assert status == "insufficient" and "永久" not in facets[0].explanation
    assert not facets[0].evidence_ids


def test_word_wrap_is_mapped_back_to_unique_literal_source_not_fuzzy_facts():
    from app.services.future_progress import _literal_pdf_span

    source = "Future work should measure slow- downs from paging."
    assert _literal_pdf_span("slow-downs from paging", source) == "slow- downs from paging"
    assert _literal_pdf_span("Future work", "Future   work") == "Future   work"
    assert _literal_pdf_span("The value is 0.7", "The value is 0.25") is None
    assert _literal_pdf_span("x-y", "x- y") is None
    assert _literal_pdf_span("slow-downs", "slow- downs and slow- downs") is None


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from app.services.storage import settings

    parsed = tmp_path / "parsed"
    parsed.mkdir()
    monkeypatch.setattr(settings, "parsed_dir", parsed)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for index, (key, year, text) in enumerate(
            [
                ("old", "1701.00001", GOAL),
                ("new", "2301.00001", PROGRESS),
                ("earlier", "1601.00001", PROGRESS),
                ("unknown", None, PROGRESS),
            ]
        ):
            db.add(
                Document(
                    id=key,
                    title=key,
                    original_filename=key + ".pdf",
                    storage_key=key + ".pdf",
                    size_bytes=1,
                    sha256=str(index) * 64,
                    status=DocumentStatus.READY,
                    arxiv_id=year,
                )
            )
            (parsed / (key + ".json")).write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page_number": 1,
                                "blocks": [
                                    {
                                        "block_id": "heading",
                                        "type": "heading",
                                        "text": "Future Work" if key == "old" else "Abstract",
                                    },
                                    {
                                        "block_id": "body",
                                        "type": "text",
                                        "text": text,
                                        "bbox": [1, 2, 3, 4],
                                    },
                                ],
                            }
                        ],
                        "references": [],
                    }
                ),
                encoding="utf-8",
            )
        db.commit()
        yield db, parsed


def request(key="new"):
    return FutureWorkAssessmentRequest(
        source=FutureWorkSource(document_id="old", page_number=1, block_ids=["body"], quote=GOAL),
        progress=[
            FutureWorkSource(
                document_id=key,
                page_number=1,
                block_ids=["body"],
                quote=PROGRESS if key != "old" else GOAL,
            )
        ],
    )


def direction():
    return direction_key("old", 1, ["body"], GOAL)


class Provider:
    name, model, calls = "stub", "test", 0

    async def assess_future_work(self, source, progress):
        self.calls += 1
        assert source == GOAL and len(progress) == 1
        return raw()


@pytest.mark.parametrize("key", ["old", "earlier", "unknown"])
def test_invalid_timeline_is_rejected_before_model(corpus, key):
    db, _ = corpus
    provider = Provider()
    with pytest.raises(ValueError):
        asyncio.run(assess_progress(db, direction(), request(key), provider))
    assert provider.calls == 0


def test_assessment_persists_and_stale_source_does_not_survive(corpus):
    db, parsed = corpus
    provider = Provider()
    result = asyncio.run(assess_progress(db, direction(), request(), provider))
    assert result.status == "partial_progress" and not result.automatically_resolved
    assert provider.calls == 1 and read_assessment(db, direction()) == result
    (parsed / "new.json").write_text('{"pages":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_assessment(db, direction())


def test_multiple_proofs_from_same_source_survive_persist_and_read(corpus):
    db, _ = corpus

    class Multiple(Provider):
        async def assess_future_work(self, source, progress):
            self.calls += 1
            data = raw(quote="We present a retrieval method for images")
            data["facets"][0]["proof"].append({
                "evidence_id": "P1", "quote": "evaluate image retrieval experiments.",
            })
            return data

    result = asyncio.run(assess_progress(db, direction(), request(), Multiple()))
    assert len(result.facets[0].proof) == 2
    assert result.facets[0].evidence_ids == ["P1"]
    assert read_assessment(db, direction()) == result


def test_bad_generation_never_overwrites_previous_valid_record(corpus):
    db, _ = corpus
    asyncio.run(assess_progress(db, direction(), request(), Provider()))
    before = db.get(FutureWorkAssessment, direction()).result_json

    class Invalid(Provider):
        async def assess_future_work(self, source, progress):
            self.calls += 1
            return {"facets": []}

    provider = Invalid()
    with pytest.raises(LLMResponseError):
        asyncio.run(assess_progress(db, direction(), request(), provider))
    assert provider.calls == 1
    assert db.get(FutureWorkAssessment, direction()).result_json == before


def test_deleting_source_cleans_owned_assessment_without_touching_later_paper(corpus, monkeypatch):
    from app.api.routes import documents as routes

    db, _ = corpus
    asyncio.run(assess_progress(db, direction(), request(), Provider()))
    monkeypatch.setattr(routes, "delete_document_index_safely", lambda key: None)
    monkeypatch.setattr(routes.storage, "delete", lambda *args: None)
    routes._delete_document_record(db, db.get(Document, "old"))
    db.commit()
    assert db.get(FutureWorkAssessment, direction()) is None
    assert db.get(Document, "new") is not None


def test_provider_uses_one_request_and_strict_format_without_retry():
    from app.llm.deepseek_responses import DeepSeekResponsesProvider

    provider = DeepSeekResponsesProvider(
        api_key="test-placeholder", model="test", base_url="https://example.test", timeout_seconds=1
    )
    calls = []

    async def response(payload):
        calls.append(payload)
        wire = raw()
        wire["facets"][0].pop("goal_quote")
        wire["facets"][0]["goal_span_id"] = "S0.g1"
        wire["facets"][0]["proof"] = [{"span_id": "P1"}]
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(wire)}]}
            ]
        }

    provider._post_response = response
    result = asyncio.run(
        provider.assess_future_work(GOAL, [{"evidence_id": "P1", "quote": PROGRESS}])
    )
    assert result == raw() and len(calls) == 1
    assert calls[0]["text"]["format"]["schema"]["additionalProperties"] is False
    assert "each separately testable component" in calls[0]["instructions"]
    assert "Do not invent a universal quantifier" in calls[0]["instructions"]
    assert "not evidence that an entire paper never tested" in calls[0]["instructions"]
    payload = json.loads(calls[0]["input"])
    assert payload["selectable_goal_spans"] == [{"span_id": "S0.g1", "quote": GOAL}]
    assert payload["later_passages"][0]["selectable_proof_spans"][0] == {
        "span_id": "P1", "quote": PROGRESS,
    }


def test_completed_context_is_not_an_eligible_goal_or_a_cached_assessment():
    source, progress = anchors()
    completed = "We already measured the speed for 29 samples."
    source = source.model_copy(update={"quote": completed + " " + GOAL})
    with pytest.raises(LLMResponseError):
        validate_assessment(raw(goal=completed, status="not_established"), source, progress)
    assert validate_assessment(raw(), source, progress)[0] == "partial_progress"


def test_goal_coverage_ignores_completed_context_but_keeps_all_explicit_plans():
    source, progress = anchors()
    source = source.model_copy(update={"quote": "We measured existing baselines. " + GOAL})
    assert validate_assessment(raw(status="addressed"), source, progress)[2]
    source = source.model_copy(update={
        "quote": source.quote + " Future work should evaluate security."
    })
    status, _, coverage, _ = validate_assessment(raw(status="addressed"), source, progress)
    assert not coverage and status == "partial_progress"


def test_current_study_contrast_is_context_but_two_future_plans_are_both_preserved():
    from app.core.future_work import future_goal_quotes

    text = "In this work we do not adapt the model, but model adaptation is left to future work."
    assert future_goal_quotes(text) == ["model adaptation is left to future work."]
    compound = "We plan to measure images, but we plan to measure audio in future work."
    assert future_goal_quotes(compound) == [compound]


def test_future_work_title_alone_is_not_a_research_goal():
    from app.core.future_work import future_goal_quotes, is_future_plan

    for title in ("Future Work", "Limitations & Future Work.", "8 Future directions:", "未来工作"):
        assert not is_future_plan(title) and future_goal_quotes(title) == []


@pytest.mark.parametrize("checks,excluded", [
    ({"scope_check": "supported", "facet_checks": ["supported"]}, True),
    ({"scope_check": "uncertain", "facet_checks": ["supported"]}, False),
    ({"scope_check": "supported", "facet_checks": ["unsupported"]}, False),
    ({"scope_check": "supported", "facet_checks": []}, False),
])
def test_automatic_exclusion_requires_complete_independent_review_and_survives_read(
    corpus, checks, excluded
):
    db, _ = corpus

    class Complete(Provider):
        review_calls = 0

        async def assess_future_work(self, source, progress):
            self.calls += 1
            return raw(status="addressed")

        async def review_future_exclusion(self, source, progress, facets):
            self.review_calls += 1
            return checks

    provider = Complete()
    result = asyncio.run(assess_progress(db, direction(), request(), provider))
    assert result.excluded_from_recommendations is excluded
    assert not result.automatically_resolved  # No universal resolution assertion.
    assert provider.calls == provider.review_calls == 1
    assert read_assessment(db, direction()) == result
    if excluded:
        row = db.get(FutureWorkAssessment, direction())
        tampered = json.loads(row.result_json)
        tampered["exclusion_review"]["scope_check"] = "uncertain"
        row.result_json = json.dumps(tampered)
        db.commit()
        with pytest.raises(ValueError):
            read_assessment(db, direction())


def test_exclusion_review_failure_does_not_hide_goal_or_retry(corpus):
    db, _ = corpus

    class Offline(Provider):
        review_calls = 0

        async def assess_future_work(self, source, progress):
            return raw(status="addressed")

        async def review_future_exclusion(self, source, progress, facets):
            self.review_calls += 1
            raise LLMResponseError("offline")

    provider = Offline()
    result = asyncio.run(assess_progress(db, direction(), request(), provider))
    assert result.status == "supported_progress" and not result.excluded_from_recommendations
    assert result.exclusion_review.status == "unavailable" and provider.review_calls == 1


def test_source_changed_during_second_review_is_not_persisted(corpus):
    db, parsed = corpus

    class Changed(Provider):
        async def assess_future_work(self, source, progress):
            return raw(status="addressed")

        async def review_future_exclusion(self, source, progress, facets):
            (parsed / "new.json").write_text('{"pages":[]}', encoding="utf-8")
            return {"scope_check": "supported", "facet_checks": ["supported"]}

    with pytest.raises(ValueError):
        asyncio.run(assess_progress(db, direction(), request(), Changed()))
    assert db.get(FutureWorkAssessment, direction()) is None
