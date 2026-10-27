from types import SimpleNamespace

import fitz
import pytest

from scripts.evaluate_official_retrieval import (
    ReadOnlyEvaluationIndex,
    anchor_valid,
    isolated_collection_base,
    local_url,
    normalized,
    source_probe,
    verify_restored_chunks,
)


@pytest.mark.parametrize("value", ["https://api.deepseek.com", "http://example.org",
                                  "http://user:secret@localhost:8001", "http://localhost/?key=x"])
def test_retrieval_test_is_local_only(value):
    with pytest.raises(ValueError):
        local_url(value)


def test_source_gold_must_be_visible_in_independent_native_page():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 90), "Trained on 400 GPUs.")
        gold = source_probe(document, 1, "400 GPUs")
        assert gold["page_number"] == 1
        assert len(gold["native_block_bbox"]) == 4
        with pytest.raises(ValueError, match="absent"):
            source_probe(document, 1, "800 GPUs")
        with pytest.raises(ValueError, match="Invalid"):
            source_probe(document, 2, "400 GPUs")


def test_source_typography_normalization_does_not_change_numbers():
    assert normalized("Jeﬀ Rasley pre-\ntrained") == normalized("Jeff Rasley pretrained")
    assert normalized("400 GPUs") != normalized("800 GPUs")
    assert normalized("1.3B") != normalized("13B")


def test_anchors_require_same_document_and_real_source_block():
    ast = {"pages": [{"width": 600, "height": 800,
                      "blocks": [{"block_id": "p1-b1", "bbox": [60, 90, 200, 110]}]}]}
    item = SimpleNamespace(document_id="doc", page_number=1, bbox=[60, 90, 200, 110],
                           block_ids=["ref-1", "p1-b1"])
    assert anchor_valid(item, "doc", ast)
    assert not anchor_valid(item, "wrong-doc", ast)
    item.block_ids = ["p1-fake"]
    assert not anchor_valid(item, "doc", ast)


def test_structured_table_anchor_requires_existing_node_page_and_coordinates():
    ast = {"pages": [{"width": 600, "height": 800, "blocks": []}],
           "tables": [{"table_id": "p1-table-1", "page_number": 1,
                       "bbox": [60, 90, 200, 130]}]}
    item = SimpleNamespace(document_id="doc", page_number=1, bbox=[60, 90, 200, 130],
                           block_ids=["p1-table-1"])
    assert anchor_valid(item, "doc", ast)
    item.block_ids = ["p1-table-2"]
    assert not anchor_valid(item, "doc", ast)
    item.block_ids = ["p1-table-1"]
    item.bbox = [300, 400, 500, 600]
    assert not anchor_valid(item, "doc", ast)
    item.block_ids = ["p1-b1"]
    item.bbox[2] = float("nan")
    assert not anchor_valid(item, "doc", ast)


def test_read_only_reuse_rejects_production_namespace_or_unfinished_run():
    import hashlib

    base = "official_remaining_bge_" + "a" * 32
    suffix = "_semantic_" + hashlib.sha256(b"BAAI/bge-m3").hexdigest()[:10] + "_1024"
    report = {"status": "completed", "model_calls": 0, "production_library_modified": False,
              "temporary_vector_collection": base + suffix,
              "documents": [{"filename": "paper.pdf"}], "cases": [{"case_id": "old"}]}
    assert isolated_collection_base(report, query_only=True) == base
    with pytest.raises(ValueError, match="index-stage"):
        isolated_collection_base(report, query_only=False)
    with pytest.raises(ValueError, match="completed"):
        isolated_collection_base({**report, "status": "running"}, query_only=True)
    with pytest.raises(ValueError, match="isolated collection"):
        isolated_collection_base({**report, "temporary_vector_collection": "production" + suffix},
                                query_only=True)
    with pytest.raises(ValueError, match="cloud-free"):
        isolated_collection_base({**report, "model_calls": 1}, query_only=True)


def test_read_only_index_refuses_mutation_even_without_a_client():
    index = ReadOnlyEvaluationIndex.__new__(ReadOnlyEvaluationIndex)
    with pytest.raises(ValueError, match="forbids indexing"):
        index.index_document(None, "doc")
    with pytest.raises(ValueError, match="forbids deletion"):
        index.delete_document("doc")


def test_restored_vector_payloads_must_match_pages_quotes_coordinates_and_chunk_ids():
    draft = SimpleNamespace(page_number=3, section="Abstract", text="Native source",
                            block_ids=["p3-b1"], bbox=[10, 20, 300, 40])
    payload = {"page_number": 3, "section": "Abstract", "text": "Native source",
               "block_ids": ["p3-b1"], "bbox": [10, 20, 300, 40], "chunk_id": "chunk"}
    point = SimpleNamespace(id="chunk", payload=payload)
    verify_restored_chunks([point], [draft])
    for patch in [{"page_number": 4}, {"text": "Altered source"}, {"chunk_id": "other"},
                  {"bbox": [20, 30, 400, 60]}]:
        with pytest.raises(ValueError, match="payloads differ"):
            verify_restored_chunks([SimpleNamespace(id="chunk", payload=payload | patch)], [draft])
