import fitz

from app.parsers.diagnostics import PdfContentKind, inspect_pdf
from scripts.build_pdf_fixtures import build_fixtures
from scripts.evaluate_pdf_corpus import evaluate_pdf


def _make_source(path) -> None:
    document = fitz.open()
    for number in range(2):
        page = document.new_page()
        page.insert_text((72, 72), f"Regression fixture page {number + 1} with native text.")
    document.save(path)
    document.close()


def test_builds_scanned_and_exact_length_stress_fixtures(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    _make_source(source)

    outputs = build_fixtures(source, tmp_path / "corpus", scan_pages=1, long_pages=5)

    assert inspect_pdf(str(outputs["scan"])).content_kind == PdfContentKind.SCANNED_IMAGE
    with fitz.open(outputs["long"]) as document:
        assert len(document) == 5
    result = evaluate_pdf(
        outputs["native"],
        {"min_pages": 2, "min_text_chars": 20, "min_extracted_text_chars": 20},
    )
    assert result["status"] == "passed"
    assert result["page_count"] == 2
