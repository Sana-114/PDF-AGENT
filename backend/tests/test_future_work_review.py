import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document, DocumentStatus
from app.schemas.review import FutureWorkDecisionRequest, FutureWorkSource
from app.services.evidence_review import EvidenceReviewService
from app.services.future_work_review import direction_key, save_decision

FUTURE = "Future work should extend retrieval to multimodal documents."
PROGRESS = "We present retrieval for multimodal documents, including visual evidence."


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        documents = []
        for index, (key, arxiv_id) in enumerate(
            [
                ("old", "1701.00001"),
                ("new", "2301.00001"),
                ("earlier", "1601.00001"),
                ("unknown", None),
            ]
        ):
            document = Document(
                id=key,
                original_filename=f"{key}.pdf",
                storage_key=f"{key}.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256=str(index) * 64,
                status=DocumentStatus.READY,
                title=key,
                arxiv_id=arxiv_id,
            )
            documents.append(document)
            db.add(document)
            payload = {
                "pages": [
                    {
                        "page_number": 1,
                        "blocks": [
                            {
                                "block_id": f"{key}-heading",
                                "type": "heading",
                                "text": "Future Work" if key == "old" else "Abstract",
                            },
                            {
                                "block_id": f"{key}-body",
                                "type": "text",
                                "bbox": [1, 2, 3, 4],
                                "text": FUTURE if key == "old" else PROGRESS,
                            },
                        ],
                    }
                ],
                "references": [],
            }
            storage_module.storage.parsed_path(key).write_text(
                json.dumps(payload), encoding="utf-8"
            )
        db.commit()
        yield db, documents, storage_module


def _source(key="old", quote=FUTURE):
    return FutureWorkSource(document_id=key, page_number=1, block_ids=[f"{key}-body"], quote=quote)


def _request(decision="confirmed_addressed", progress_key="new"):
    return FutureWorkDecisionRequest(
        source=_source(),
        decision=decision,
        reason="人工核查后续论文的任务、方法及实验边界。",
        progress=[_source(progress_key, PROGRESS)] if progress_key else [],
    )


def _key():
    return direction_key("old", 1, ["old-body"], FUTURE)


def test_saved_confirmation_survives_review_reordering_and_can_reopen(corpus):
    db, documents, _ = corpus
    result = save_decision(db, _key(), _request())
    assert result.progress_evidence[0].quote == PROGRESS
    assert result.progress_evidence[0].bbox == [1, 2, 3, 4]
    review = asyncio.run(
        EvidenceReviewService(db, provider=ExtractiveProvider()).generate(list(reversed(documents)))
    )
    direction = next(item for item in review.future_directions if item.source_document_id == "old")
    assert direction.direction_id == _key()
    assert direction.status == "confirmed_addressed" and direction.exploration_score == 0
    save_decision(db, _key(), _request("open", None))
    reopened = asyncio.run(
        EvidenceReviewService(db, provider=ExtractiveProvider()).generate(documents)
    )
    assert reopened.future_directions[0].status == "open"


@pytest.mark.parametrize("progress_key", [None, "old", "earlier", "unknown"])
def test_confirmation_rejects_missing_same_old_or_unknown_year_evidence(corpus, progress_key):
    db, _, _ = corpus
    request = _request(progress_key=progress_key)
    if progress_key == "old":
        request.progress = [_source()]
    with pytest.raises(ValueError):
        save_decision(db, _key(), request)


def test_forged_quote_wrong_page_and_arbitrary_direction_rejected(corpus):
    db, _, _ = corpus
    request = _request()
    request.progress[0].quote = "This fabricated result completely solves the problem."
    with pytest.raises(ValueError, match="连续文本"):
        save_decision(db, _key(), request)
    request = _request()
    request.progress[0].page_number = 9
    with pytest.raises(ValueError, match="页码"):
        save_decision(db, _key(), request)
    with pytest.raises(ValueError, match="ID"):
        save_decision(db, "future-forged", _request())


def test_changed_followup_no_longer_excludes_direction(corpus):
    db, documents, storage_module = corpus
    save_decision(db, _key(), _request())
    storage_module.storage.parsed_path("new").write_text('{"pages":[]}', encoding="utf-8")
    review = asyncio.run(
        EvidenceReviewService(db, provider=ExtractiveProvider()).generate(documents)
    )
    assert review.future_directions[0].status != "confirmed_addressed"
    assert review.future_directions[0].confirmation is None
    assert any("已变更或移除" in warning for warning in review.warnings)


def test_unknown_year_is_allowed_only_as_needs_review(corpus):
    db, _, _ = corpus
    result = save_decision(db, _key(), _request("needs_review", "unknown"))
    assert result.decision == "needs_review"


def test_whitespace_note_is_not_a_confirmation():
    with pytest.raises(ValueError):
        FutureWorkDecisionRequest(source=_source(), decision="open", reason=" " * 20)
    with pytest.raises(ValueError):
        _source("new", " " * 20)
