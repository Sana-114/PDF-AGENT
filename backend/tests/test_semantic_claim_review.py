import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import GeneratedAnswer, GeneratedClaim, LLMResponseError
from app.llm.claim_review import validate_verdicts
from app.models.document import Document, DocumentStatus
from app.services.evidence_review import EvidenceReviewService


@pytest.mark.parametrize(
    "verdicts",
    [
        [],
        [{"claim_index": 0, "status": "supported", "reason": "ok"}] * 2,
        [{"claim_index": True, "status": "supported", "reason": "ok"}],
        [{"claim_index": 0, "status": "supported", "reason": "ok", "text": "replacement"}],
    ],
)
def test_verdicts_cannot_omit_duplicate_or_rewrite_claims(verdicts):
    with pytest.raises(LLMResponseError):
        validate_verdicts({"verdicts": verdicts}, 1)


@pytest.mark.parametrize("failed", [False, True])
def test_review_keeps_only_original_supported_claims_or_fails_closed(tmp_path, monkeypatch, failed):
    from app.services.storage import settings

    parsed = tmp_path / "parsed"
    parsed.mkdir()
    monkeypatch.setattr(settings, "parsed_dir", parsed)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    class Provider:
        name, model, review_calls = "stub", "test", 0

        async def generate_grounded_answer(self, question, evidence):
            return GeneratedAnswer(
                answer="",
                claims=[
                    GeneratedClaim("Original supported statement.", ["R1"]),
                    GeneratedClaim("Original unsupported statement.", ["R1"]),
                ],
            )

        async def review_claims(self, claims, evidence):
            self.review_calls += 1
            if failed:
                raise LLMResponseError("No valid reviewer output")
            return [
                {"claim_index": 0, "status": "supported", "reason": "original supports it"},
                {"claim_index": 1, "status": "unsupported", "reason": "scope overstatement"},
            ]

    provider = Provider()
    with Session(engine) as db:
        doc = Document(
            id="one",
            title="Original",
            original_filename="one.pdf",
            storage_key="one.pdf",
            size_bytes=1,
            sha256="a" * 64,
            status=DocumentStatus.READY,
            arxiv_id="1701.00001",
        )
        db.add(doc)
        db.commit()
        (parsed / "one.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "page_number": 1,
                            "blocks": [
                                {"block_id": "h", "type": "heading", "text": "Abstract"},
                                {
                                    "block_id": "b",
                                    "type": "text",
                                    "bbox": [1, 2, 3, 4],
                                    "text": "Original supported statement.",
                                },
                            ],
                        }
                    ],
                    "references": [],
                }
            ),
            encoding="utf-8",
        )
        result = asyncio.run(EvidenceReviewService(db, provider=provider).generate([doc]))
    assert provider.review_calls == 1
    assert result.semantic_review.status == ("unavailable" if failed else "checked")
    if failed:
        assert not result.claims and result.insufficient_evidence
    else:
        assert [c.text for c in result.claims] == ["Original supported statement."]
        assert result.semantic_review.accepted_claim_count == 1
