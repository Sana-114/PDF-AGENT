from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.document import Document, DocumentStatus
from app.services.chunking import build_chunk_drafts, replace_document_chunks
from app.services.retrieval import LexicalRetriever


def _parsed_document() -> dict:
    return {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {
                        "block_id": "p1-b1",
                        "type": "heading",
                        "text": "5 Training",
                        "bbox": [10, 10, 200, 30],
                    },
                    {
                        "block_id": "p1-b2",
                        "type": "text",
                        "text": "We used beta_1 = 0.9 and beta_2 = 0.98 for the Adam optimizer.",
                        "bbox": [10, 40, 300, 80],
                    },
                ],
            },
            {
                "page_number": 2,
                "blocks": [
                    {
                        "block_id": "p2-b1",
                        "type": "heading",
                        "text": "6 Results",
                        "bbox": [10, 10, 200, 30],
                    },
                    {
                        "block_id": "p2-b2",
                        "type": "text",
                        "text": "The model achieved 28.4 BLEU on the translation task.",
                        "bbox": [10, 40, 300, 80],
                    },
                ],
            },
        ]
    }


def test_chunking_keeps_page_and_block_anchors() -> None:
    drafts = build_chunk_drafts(_parsed_document())

    assert len(drafts) == 2
    assert drafts[0].page_number == 1
    assert drafts[0].block_ids == ["p1-b1", "p1-b2"]
    assert drafts[0].bbox == [10.0, 10.0, 300.0, 80.0]


def test_lexical_retrieval_returns_ranked_evidence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = Document(
            original_filename="paper.pdf",
            storage_key="paper.pdf",
            size_bytes=100,
            sha256="a" * 64,
            status=DocumentStatus.READY,
            title="Example Paper",
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _parsed_document())
        session.commit()

        results = LexicalRetriever(session).search(
            "What beta_1 value did the optimizer use?", [document.id], top_k=2
        )

    assert results
    assert results[0].page_number == 1
    assert results[0].block_ids == ["p1-b1", "p1-b2"]
    assert "0.9" in results[0].quote
    assert results[0].evidence_id == "E1"

