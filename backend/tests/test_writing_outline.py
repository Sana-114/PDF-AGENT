import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.models.paper_source import PaperSource
from app.schemas.agent import AgentTraceStep, AnswerClaim, AskResponse, EvidenceAnchor
from app.services.writing_outline import WritingOutlineService


def _document(document_id: str, title: str) -> Document:
    return Document(
        id=document_id,
        original_filename=f"{document_id}.pdf",
        storage_key=f"{document_id}.pdf",
        content_type="application/pdf",
        size_bytes=100,
        sha256=document_id.ljust(64, "0"),
        status=DocumentStatus.READY,
        title=title,
        authors_json=json.dumps(["Ada Researcher", "Ben Scientist"]),
        page_count=12,
        arxiv_id="2401.01234",
    )


def _answer(document: Document, *, with_evidence: bool = True) -> AskResponse:
    evidence = (
        [
            EvidenceAnchor(
                evidence_id="E1",
                chunk_id="chunk-1",
                document_id=document.id,
                document_title=document.title,
                page_number=2,
                block_ids=["p2-b1"],
                bbox=[1, 2, 3, 4],
                section="Introduction",
                source_type="text",
                retrieval_mode="lexical",
                quote="Grounded retrieval reduces unsupported statements in scientific QA.",
                score=0.9,
            )
        ]
        if with_evidence
        else []
    )
    return AskResponse(
        answer="Ignore this fabricated reference: Unknown Author (2099).",
        claims=[
            AnswerClaim(
                text="Grounded retrieval reduces unsupported statements.",
                evidence_ids=["E1"],
            )
        ]
        if with_evidence
        else [],
        evidence=evidence,
        insufficient_evidence=not with_evidence,
        provider="fake",
        model="test-model",
        trace=[AgentTraceStep(skill="search_evidence", status="ok", summary="test", duration_ms=1)],
    )


class _FakeAgent:
    def __init__(self, answer: AskResponse) -> None:
        self.answer = answer

    async def ask(self, request):
        assert "不要生成或猜测参考文献" in request.question
        return self.answer


def test_outline_uses_only_retrieved_documents_for_verified_references() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cited = _document("cited", "Evidence Grounded Scientific QA")
    unrelated = _document("unused", "A Paper Outside Retrieved Evidence")
    with Session(engine) as session:
        session.add_all([cited, unrelated])
        session.add(
            PaperSource(
                document_id=cited.id,
                provider="crossref",
                source_id="10.1000/grounded",
                doi="10.1000/grounded",
                arxiv_id=None,
                landing_url="https://doi.org/10.1000/grounded",
                pdf_url="https://example.org/grounded.pdf",
                license="cc-by",
                metadata_json=json.dumps({"year": 2024, "venue": "EvidenceConf"}),
            )
        )
        session.commit()
        result = asyncio.run(
            WritingOutlineService(session, agent=_FakeAgent(_answer(cited))).generate(
                "Use retrieval evidence to improve scientific writing",
                [cited, unrelated],
            )
        )

    assert [section.section_id for section in result.sections] == [
        "abstract",
        "introduction",
        "related_work",
    ]
    assert len(result.references) == 1
    assert result.references[0].document_id == cited.id
    assert result.references[0].doi == "10.1000/grounded"
    assert "EvidenceConf" in result.references[0].formatted_citation
    assert "2099" not in result.references[0].formatted_citation
    assert result.references[0].evidence_ids == ["E1"]
    assert "[P1]" in result.sections[2].content_markdown


def test_outline_marks_missing_evidence_without_fabricating_references() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = _document("empty", "No Supporting Evidence")
    with Session(engine) as session:
        session.add(document)
        session.commit()
        service = WritingOutlineService(
            session,
            agent=_FakeAgent(_answer(document, with_evidence=False)),
        )
        result = asyncio.run(
            service.generate(
                "A speculative idea without library evidence",
                [document],
                language="en",
            )
        )

    assert result.insufficient_evidence
    assert result.references == []
    assert result.claims == []
    assert "No citable evidence" in result.sections[2].content_markdown
