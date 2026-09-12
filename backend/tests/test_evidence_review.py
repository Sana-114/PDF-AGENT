import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import GeneratedAnswer, GeneratedClaim
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document, DocumentStatus
from app.services.evidence_review import EvidenceReviewService


def _document(document_id: str, title: str, arxiv_id: str | None) -> Document:
    return Document(
        id=document_id,
        original_filename=f"{document_id}.pdf",
        storage_key=f"{document_id}.pdf",
        content_type="application/pdf",
        size_bytes=100,
        sha256=document_id.ljust(64, "0"),
        status=DocumentStatus.READY,
        title=title,
        authors_json=json.dumps([f"Author {document_id.upper()}"]),
        page_count=8,
        arxiv_id=arxiv_id,
    )


def _block(block_id: str, text: str, *, kind: str = "text", level: int | None = None):
    payload = {
        "block_id": block_id,
        "text": text,
        "type": kind,
        "bbox": [10, 20, 500, 60],
    }
    if level:
        payload["level"] = level
    return payload


def _write_parsed(storage_module, document_id: str, pages: list[dict], references=None):
    storage_module.storage.parsed_path(document_id).write_text(
        json.dumps(
            {
                "title": document_id,
                "pages": pages,
                "references": references or [],
            }
        ),
        encoding="utf-8",
    )


def test_future_work_is_flagged_only_with_later_progress_evidence(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    old = _document("old", "Retrieval for Text Documents", "1701.00001")
    new = _document("new", "Multimodal Document Retrieval", "2301.00001")
    _write_parsed(
        storage_module,
        old.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("o-h1", "Abstract", kind="heading", level=1),
                    _block("o-a1", "We present a retrieval method for text documents."),
                ],
            },
            {
                "page_number": 8,
                "blocks": [
                    _block("o-h2", "Future Work", kind="heading", level=1),
                    _block(
                        "o-f1",
                        "Future work should extend retrieval to multimodal documents.",
                    ),
                ],
            },
        ],
    )
    _write_parsed(
        storage_module,
        new.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("n-h1", "Abstract", kind="heading", level=1),
                    _block("n-a1", "We present retrieval for multimodal documents."),
                ],
            }
        ],
        references=[
            {
                "reference_id": "ref-1",
                "label": "1",
                "text": "Retrieval for Text Documents. 2017.",
                "page_number": 7,
                "block_ids": ["n-r1"],
            }
        ],
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([old, new])
        session.commit()
        result = asyncio.run(
            EvidenceReviewService(session, provider=ExtractiveProvider()).generate([old, new])
        )

    assert result.graph_stats.relation_count == 1
    assert len(result.future_directions) == 1
    direction = result.future_directions[0]
    assert direction.status == "possibly_addressed"
    assert direction.possibly_addressed_by_document_ids == [new.id]
    assert direction.progress_evidence_ids
    assert direction.exploration_score < 0.3


def test_review_discards_claims_without_valid_evidence_ids(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    class FakeProvider:
        name = "fake"
        model = "grounded-test"
        supports_translation = False

        async def generate_grounded_answer(self, question, evidence):
            assert "领域综述" in question
            assert evidence[0].evidence_id == "R1"
            return GeneratedAnswer(
                answer="Evidence-grounded review.",
                claims=[
                    GeneratedClaim(text="Supported", evidence_ids=["R1", "invented"]),
                    GeneratedClaim(text="Unsupported", evidence_ids=["invented"]),
                ],
            )

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    document = _document("paper", "Grounded Review Systems", None)
    _write_parsed(
        storage_module,
        document.id,
        [
            {
                "page_number": 1,
                "blocks": [
                    _block("p-h1", "Abstract", kind="heading", level=1),
                    _block(
                        "p-a1",
                        "We present an evidence-grounded review system with stable anchors.",
                    ),
                ],
            }
        ],
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(document)
        session.commit()
        result = asyncio.run(
            EvidenceReviewService(session, provider=FakeProvider()).generate([document])
        )

    assert result.review == "Evidence-grounded review."
    assert result.provider == "fake"
    assert [claim.text for claim in result.claims] == ["Supported"]
    assert result.claims[0].evidence_ids == ["R1"]
    assert not result.insufficient_evidence
