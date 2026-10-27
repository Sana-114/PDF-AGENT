import hashlib
import json

import pytest

from app.schemas.agent import EvidenceAnchor
from scripts.review_official_retrieval import review


def save_json(path, value, *, indent=None):
    path.write_text(json.dumps(value, indent=indent), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_reports(tmp_path):
    parser_root = tmp_path / "parser"
    ast_dir = parser_root / "document-01/artifacts"
    ast_dir.mkdir(parents=True)
    ast = {"pages": [{"page_number": 1, "width": 600, "height": 800, "blocks": []}],
           "tables": [{"table_id": "p1-table-1", "page_number": 1,
                       "bbox": [60, 90, 200, 130]}]}
    ast_hash = save_json(ast_dir / "parsed.json", ast, indent=2)
    parse_report = parser_root / "report.json"
    parse_hash = save_json(parse_report, {"documents": [{"filename": "paper.pdf",
                              "artifacts_directory": "document-01"}]})
    root = tmp_path / "retrieval"
    (root / "parsed").mkdir(parents=True)
    save_json(root / "parsed/doc.json", ast)  # Same AST, different JSON whitespace.
    evidence = EvidenceAnchor(evidence_id="E1", document_id="doc", page_number=1,
        block_ids=["p1-table-1"], bbox=[60, 90, 200, 130], quote="400 GPUs", score=.9,
        retrieval_mode="reranked", recall_mode="hybrid", source_type="table")
    source = root / "report.json"
    save_json(source, {"status": "completed", "inputs_sha256": {"parse_report": parse_hash},
        "summary": {"cases": 1, "passed": 0, "failed": 1}, "documents": [{
            "filename": "paper.pdf", "document_id": "doc", "ast_sha256": ast_hash}],
        "cases": [{"filename": "paper.pdf", "status": "failed",
                   "failures": ["invalid_or_out_of_scope_source_anchor"],
                   "gold": {"page_number": 1, "quote": "400 GPUs"},
                   "evidence": [evidence.model_dump(mode="json")]}]})
    return source, parse_report


def test_review_accepts_real_structured_node_and_preserves_old_failure(tmp_path):
    source, parser = fixture_reports(tmp_path)
    original = source.read_bytes()
    result = review(source, tmp_path / "reviewed.json", parser)
    assert result["summary"]["passed"] == 1
    assert result["cases"][0]["original_status"] == "failed"
    assert result["model_calls"] == result["embedding_calls"] == result["reranker_calls"] == 0
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="overwrite"):
        review(source, tmp_path / "reviewed.json", parser)


def test_review_rejects_altered_ast_copy(tmp_path):
    source, parser = fixture_reports(tmp_path)
    path = source.parent / "parsed/doc.json"
    ast = json.loads(path.read_text())
    ast["tables"][0]["bbox"] = [60, 90, 300, 400]
    save_json(path, ast)
    with pytest.raises(ValueError, match="contents differ"):
        review(source, tmp_path / "reviewed.json", parser)
    assert not (tmp_path / "reviewed.json").exists()
