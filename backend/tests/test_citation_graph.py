import json

from app.models.document import Document, DocumentStatus
from app.services.citation_graph import CitationGraphService, normalize_title, title_terms


def _document(document_id: str, title: str, *, arxiv_id: str | None = None) -> Document:
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
        page_count=10,
        arxiv_id=arxiv_id,
    )


def _write_parsed(storage_module, document_id: str, references: list[dict]) -> None:
    storage_module.storage.parsed_path(document_id).write_text(
        json.dumps(
            {
                "title": document_id,
                "references": references,
                "pages": [],
            }
        ),
        encoding="utf-8",
    )


def _reference(index: int, text: str) -> dict:
    return {
        "reference_id": f"ref-{index}",
        "label": str(index),
        "text": text,
        "page_number": 9,
        "bbox": [1, 2, 3, 4],
        "block_ids": [f"p9-b{index}"],
    }


def test_title_normalization_is_stable_for_punctuation_and_stop_words() -> None:
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert title_terms("Attention Is All You Need!") == {"attention", "is", "all", "you", "need"}


def test_graph_builds_local_edges_and_identifies_roles(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    documents = [
        _document("seq", "Sequence to Sequence Learning with Neural Networks"),
        _document("attention", "Attention Is All You Need", arxiv_id="1706.03762"),
        _document("efficient", "Efficient Transformer Systems"),
        _document("isolated", "An Unrelated Isolated Study"),
    ]
    _write_parsed(storage_module, "seq", [])
    _write_parsed(
        storage_module,
        "attention",
        [
            _reference(
                1,
                "Sutskever et al. Sequence to Sequence Learning with Neural Networks. 2014.",
            )
        ],
    )
    _write_parsed(
        storage_module,
        "efficient",
        [
            _reference(1, "Vaswani et al. Attention Is All You Need. NeurIPS 2017."),
            _reference(2, "Sequence to Sequence Learning with Neural Networks, 2014."),
            _reference(3, "A Missing Paper That Is Not In The Local Library."),
        ],
    )
    _write_parsed(storage_module, "isolated", [])

    graph = CitationGraphService().build(documents)
    nodes = {node.document_id: node for node in graph.nodes}
    relations = {
        (edge.source_document_id, edge.target_document_id) for edge in graph.edges
    }

    assert relations == {
        ("attention", "seq"),
        ("efficient", "attention"),
        ("efficient", "seq"),
    }
    assert nodes["seq"].role == "cornerstone"
    assert nodes["seq"].in_degree == 2
    assert nodes["seq"].pagerank > nodes["attention"].pagerank
    assert nodes["attention"].role == "bridge"
    assert nodes["efficient"].role == "derivative"
    assert nodes["isolated"].role == "isolated"
    assert graph.stats.total_references == 4
    assert graph.stats.matched_references == 3
    assert graph.stats.unmatched_references == 1
    assert graph.stats.relation_count == 3


def test_arxiv_identifier_has_priority_over_title_similarity(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    source = _document("source", "A New Transformer Review")
    target = _document("target", "Attention Is All You Need", arxiv_id="1706.03762")
    _write_parsed(
        storage_module,
        source.id,
        [_reference(1, "A. Vaswani et al. arXiv:1706.03762v7")],
    )
    _write_parsed(storage_module, target.id, [])

    graph = CitationGraphService().build([source, target])

    assert len(graph.edges) == 1
    assert graph.edges[0].target_document_id == target.id
    assert graph.edges[0].match_score == 1.0
    assert graph.edges[0].match_reason == "arXiv ID 完全一致"


def test_missing_parsed_document_remains_as_warned_node(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    document = _document("missing", "Parsed Artifact Missing")

    graph = CitationGraphService().build([document])

    assert len(graph.nodes) == 1
    assert graph.nodes[0].role == "isolated"
    assert "缺少结构化解析结果" in graph.warnings[0]
