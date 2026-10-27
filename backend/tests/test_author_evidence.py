import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.models.chunk import DocumentChunk
from app.models.document import Document, DocumentStatus
from app.services.author_evidence import AUTHOR_SECTION, author_evidence_nodes
from app.services.chunking import build_chunk_drafts, ensure_document_chunks
from app.services.retrieval import LexicalRetriever


def parsed_authors():
    # Reading order intentionally crosses the author grid; AST preserves geometric order.
    return {"authors": ["Alice One", "Bob Two", "Carol Three"], "pages": [{
        "page_number": 1, "blocks": [
            {"block_id": "p1-b1", "text": "Alice One * Institute Alpha", "type": "text",
             "bbox": [10, 30, 100, 60]},
            {"block_id": "p1-b3", "text": "Carol Three * University Gamma", "type": "text",
             "bbox": [10, 70, 100, 100]},
            {"block_id": "p1-b2", "text": "Bob Two * Institute Beta", "type": "text",
             "bbox": [110, 30, 220, 60]},
        ],
    }]}


def test_author_ordinals_preserve_ast_order_and_all_real_blocks():
    node = author_evidence_nodes(parsed_authors())[0]
    assert "1. Alice One\n2. Bob Two\n3. Carol Three" in node["text"]
    assert "不是原文编号" in node["text"]
    assert "University Gamma" in node["text"]
    assert set(node["block_ids"][1:]) == {"p1-b1", "p1-b2", "p1-b3"}
    assert node["bbox"] == [10, 30, 220, 100]


def test_missing_author_does_not_generate_a_shifted_or_invented_order():
    parsed = parsed_authors()
    parsed["authors"].insert(1, "Unknown Person")
    assert author_evidence_nodes(parsed) == []


def test_native_ligature_and_missing_space_can_anchor_normalized_author():
    parsed = {"authors": ["Jeff Rasley"], "pages": [{"page_number": 1, "blocks": [
        {"block_id": "p1-b1", "text": "JeﬀRasley∗", "bbox": [60, 90, 130, 110]},
    ]}]}
    node = author_evidence_nodes(parsed)[0]
    assert "1. Jeff Rasley" in node["text"]
    assert "JeﬀRasley∗" in node["text"]
    parsed["authors"] = ["Alice One", "Alice One"]
    assert author_evidence_nodes(parsed) == []


def test_metadata_does_not_assign_every_institution_to_every_author():
    parsed = parsed_authors()
    parsed["affiliations"] = ["Invented Institution"]
    node = author_evidence_nodes(parsed)[0]
    assert "Invented Institution" not in node["text"]
    drafts = build_chunk_drafts(parsed)
    assert sum(item.section == AUTHOR_SECTION for item in drafts) == 1


def test_backfill_preserves_old_chunk_ids_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "parsed_dir", tmp_path)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        doc = Document(original_filename="test.pdf", storage_key="test.pdf", size_bytes=1,
                       sha256="a" * 64, status=DocumentStatus.READY, title="Test")
        session.add(doc)
        session.flush()
        old = DocumentChunk(document_id=doc.id, chunk_index=0, page_number=1,
                            block_ids_json='["p1-b1"]', text="Alice One", char_count=9)
        session.add(old)
        session.commit()
        old_id = old.id
        (tmp_path / f"{doc.id}.json").write_text(json.dumps(parsed_authors()), encoding="utf-8")
        assert ensure_document_chunks(session, [doc.id]) == 1
        assert ensure_document_chunks(session, [doc.id]) == 0
        assert session.get(DocumentChunk, old_id) is not None
        assert len(session.scalars(select(DocumentChunk)).all()) == 2
        evidence = LexicalRetriever(session).search("第 2 作者及机构是谁？", [doc.id], 2)
        assert evidence[0].section == AUTHOR_SECTION
        assert "2. Bob Two" in evidence[0].quote


def test_abstract_with_institution_link_is_not_an_author_affiliation_block():
    parsed = parsed_authors()
    blocks = parsed["pages"][0]["blocks"]
    blocks.extend([
        {"block_id": "ah", "type": "heading", "text": "Abstract",
         "bbox": [10, 105, 100, 115]},
        {"block_id": "ab", "type": "text",
         "text": "We train a model on a public dataset and release code at "
                 "https://github.com/openai/example. This is scientific abstract prose.",
         "bbox": [10, 118, 220, 400]},
        {"block_id": "ih", "type": "heading", "text": "1 Introduction",
         "bbox": [10, 405, 100, 415]},
        {"block_id": "note", "type": "footnote",
         "text": "*Equal contribution. Institute Alpha. Correspondence: author@example.test",
         "bbox": [10, 700, 220, 725]},
    ])
    node = author_evidence_nodes(parsed)[0]
    assert "ab" not in node["block_ids"] and "public dataset" not in node["text"]
    assert "note" in node["block_ids"] and "Correspondence" in node["text"]
    assert "3. Carol Three" in node["text"]


def test_full_author_names_in_body_are_not_front_matter_identity_proof():
    parsed = {"authors": ["Alice One"], "pages": [{"page_number": 1, "blocks": [
        {"block_id": "h", "type": "heading", "text": "1 Introduction",
         "bbox": [0, 100, 200, 120]},
        {"block_id": "body", "type": "text", "text": "Alice One previously studied this.",
         "bbox": [0, 125, 200, 140]},
    ]}]}
    assert author_evidence_nodes(parsed) == []


def test_body_code_footnote_is_not_author_credit_and_missing_heading_uses_literal_abstract():
    parsed = parsed_authors()
    abstract = "An abstract discusses a learned method and releases code through a research " \
        "organization link at https://github.com/openai/example."
    parsed["abstract"] = abstract
    blocks = parsed["pages"][0]["blocks"]
    blocks.extend([
        {"block_id": "ab", "type": "text", "text": abstract, "bbox": [10, 118, 220, 400]},
        {"block_id": "code", "type": "footnote", "text": "1 OpenAI model code is available.",
         "bbox": [10, 700, 220, 725]},
    ])
    node = author_evidence_nodes(parsed)[0]
    assert "ab" not in node["block_ids"] and "code" not in node["block_ids"]


def test_heading_shaped_author_after_body_in_reading_order_uses_original_page_geometry():
    parsed = {"authors": ["Alice One"], "pages": [{"page_number": 1, "blocks": [
        {"block_id": "title", "type": "title", "text": "Methods for Grounded Science",
         "bbox": [0, 20, 200, 40]},
        {"block_id": "h", "type": "heading", "text": "Abstract",
         "bbox": [0, 100, 200, 120]},
        {"block_id": "body", "type": "text", "text": "A scientific paragraph about models.",
         "bbox": [0, 125, 200, 200]},
        {"block_id": "author", "type": "heading", "text": "Alice One",
         "bbox": [0, 50, 200, 70]},
    ]}]}
    node = author_evidence_nodes(parsed)[0]
    assert node["block_ids"] == ["p1-metadata-authors", "author"]
    assert "scientific paragraph" not in node["text"]
