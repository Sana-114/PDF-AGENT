from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.document import Document, DocumentStatus
from app.services.chunking import (
    _needs_structured_backfill,
    build_chunk_drafts,
    replace_document_chunks,
)
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


def _structured_document() -> dict:
    parsed = _parsed_document()
    parsed.update(
        {
            "tables": [
                {
                    "table_id": "p2-table-1",
                    "page_number": 2,
                    "bbox": [20, 100, 300, 220],
                    "caption": "Table 1. Translation results",
                    "caption_block_id": "p2-b3",
                    "markdown": "| Model | BLEU |\n| --- | --- |\n| Ours | 31.2 |",
                }
            ],
            "figures": [
                {
                    "figure_id": "p2-figure-1",
                    "page_number": 2,
                    "bbox": [20, 250, 300, 400],
                    "caption": "Figure 1. Training pipeline",
                    "caption_block_id": "p2-b4",
                }
            ],
            "formulas": [
                {
                    "formula_id": "formula-1",
                    "page_number": 1,
                    "bbox": [10, 90, 300, 110],
                    "block_id": "p1-b3",
                    "text": "beta_1 = 0.9, beta_2 = 0.98",
                    "latex": r"\beta_1 = 0.9, \beta_2 = 0.98",
                }
            ],
            "references": [
                {
                    "reference_id": "ref-7",
                    "label": "7",
                    "text": "A. Author. Grounded Systems. Test Press, 2025.",
                    "page_number": 2,
                    "bbox": [10, 500, 500, 530],
                    "block_ids": ["p2-b7"],
                }
            ],
        }
    )
    return parsed


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


def test_chunking_adds_anchored_structured_nodes() -> None:
    drafts = build_chunk_drafts(_structured_document())

    table = next(draft for draft in drafts if "p2-table-1" in draft.block_ids)
    formula = next(draft for draft in drafts if "formula-1" in draft.block_ids)
    reference = next(draft for draft in drafts if "ref-7" in draft.block_ids)

    assert table.page_number == 2
    assert table.bbox == [20.0, 100.0, 300.0, 220.0]
    assert "31.2" in table.text
    assert formula.section == "公式 · formula-1"
    assert "LaTeX candidate:" in formula.text
    assert r"\beta_1" in formula.text
    assert reference.text.startswith("[7] A. Author")


def test_chunking_splits_cross_page_table_into_page_anchored_evidence() -> None:
    parsed = _parsed_document()
    parsed["tables"] = [
        {
            "table_id": "p1-table-1",
            "page_number": 1,
            "bbox": [20, 600, 300, 820],
            "caption": "Table 2. Cross-page results",
            "caption_block_id": "p1-b3",
            "rows": [
                ["Model", "BLEU"],
                ["Small", "28.4"],
                ["Large", "31.2"],
            ],
            "markdown": "full table markdown",
            "segments": [
                {
                    "page_number": 1,
                    "bbox": [20, 600, 300, 820],
                    "row_start": 0,
                    "row_end": 2,
                    "caption_block_id": "p1-b3",
                },
                {
                    "page_number": 2,
                    "bbox": [20, 40, 300, 180],
                    "row_start": 2,
                    "row_end": 3,
                    "caption_block_id": "p2-b3",
                },
            ],
        }
    ]

    drafts = build_chunk_drafts(parsed)
    segments = [draft for draft in drafts if "p1-table-1" in draft.block_ids]

    assert len(segments) == 2
    assert [draft.page_number for draft in segments] == [1, 2]
    assert segments[1].bbox == [20.0, 40.0, 300.0, 180.0]
    assert "| Model | BLEU |" in segments[1].text
    assert "| Large | 31.2 |" in segments[1].text
    assert "28.4" not in segments[1].text
    assert "p2-b3" in segments[1].block_ids


def test_retrieval_prioritizes_matching_structured_evidence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = Document(
            original_filename="structured.pdf",
            storage_key="structured.pdf",
            size_bytes=100,
            sha256="b" * 64,
            status=DocumentStatus.READY,
            title="Structured Paper",
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _structured_document())
        session.commit()

        table_results = LexicalRetriever(session).search(
            "What BLEU score did Ours obtain in Table 1?", [document.id], top_k=3
        )
        reference_results = LexicalRetriever(session).search(
            "Give the complete Grounded Systems reference citation", [document.id], top_k=3
        )

    assert table_results[0].source_type == "table"
    assert "31.2" in table_results[0].quote
    assert table_results[0].block_ids[0] == "p2-table-1"
    assert reference_results[0].source_type == "reference"
    assert reference_results[0].block_ids[0] == "ref-7"


def test_existing_text_chunks_are_detected_for_structured_backfill() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = Document(
            original_filename="upgrade.pdf",
            storage_key="upgrade.pdf",
            size_bytes=100,
            sha256="c" * 64,
            status=DocumentStatus.READY,
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _parsed_document())
        session.commit()

        assert _needs_structured_backfill(session, document.id, _structured_document())

        replace_document_chunks(session, document.id, _structured_document())
        session.commit()

        assert not _needs_structured_backfill(session, document.id, _structured_document())
