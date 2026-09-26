import hashlib

import pytest

from app.evaluation import RetrievalEvaluationSet
from scripts.evaluate_competition_pdf_rag import (
    comparison_groups,
    source_files,
    verified_pdf,
)


def test_competition_sources_require_one_safe_filename() -> None:
    dataset = RetrievalEvaluationSet.model_validate(
        {
            "dataset_id": "sample",
            "cases": [
                {
                    "case_id": "a",
                    "question": "What changed?",
                    "documents": [{"filename": "paper.pdf"}],
                    "expectations": [{"text_contains_any": ["changed"]}],
                },
                {
                    "case_id": "b",
                    "question": "What stayed?",
                    "documents": [{"filename": "paper.pdf"}],
                    "expectations": [{"text_contains_any": ["stayed"]}],
                },
            ],
        }
    )
    assert source_files(dataset) == ["paper.pdf"]
    dataset.cases[1].documents[0].filename = "../paper.pdf"
    with pytest.raises(ValueError, match="must not contain a directory"):
        source_files(dataset)


def test_competition_verifies_pdf_bytes_and_pinned_hash(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfixture")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert verified_pdf(pdf, {pdf.name: {"sha256": digest}}) == digest
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verified_pdf(pdf, {pdf.name: {"sha256": "0" * 64}})


def test_comparison_group_requires_independent_passed_papers() -> None:
    group = {"comparison_groups": [{"group_id": "pair", "case_ids": ["a", "b"]}]}
    cases = [
        {"case_id": "a", "document_ids": ["paper-a"], "passed": True},
        {"case_id": "b", "document_ids": ["paper-b"], "passed": True},
    ]
    assert comparison_groups(group, cases)[0]["passed"] is True
    cases[1]["passed"] = False
    assert comparison_groups(group, cases)[0]["passed"] is False
    cases[1]["document_ids"] = ["paper-a"]
    with pytest.raises(ValueError, match="different PDFs"):
        comparison_groups(group, cases)
