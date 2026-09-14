import fitz

from app.parsers.diagnostics import PdfContentKind, inspect_pdf
from scripts.build_pdf_fixtures import build_fixtures
from scripts.evaluate_long_document import evaluate_long_document
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
        batch_pages=1,
    )
    assert result["status"] == "passed"
    assert result["page_count"] == 2
    assert result["checkpoint_batches"] == 2
    assert result["checkpoint_progress_events"] == 2


def test_builds_only_named_scan_fixture(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    _make_source(source)

    outputs = build_fixtures(
        source,
        tmp_path / "corpus",
        scan_pages=1,
        only={"scan"},
        scan_filename="1706.03762v1_img.pdf",
    )

    assert set(outputs) == {"scan"}
    assert outputs["scan"].name == "1706.03762v1_img.pdf"
    assert inspect_pdf(str(outputs["scan"])).content_kind == PdfContentKind.SCANNED_IMAGE


def test_builds_only_named_long_fixture(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    _make_source(source)

    outputs = build_fixtures(
        source,
        tmp_path / "corpus",
        long_pages=5,
        only={"long"},
        long_filename="transformer_long_5p.pdf",
    )

    assert set(outputs) == {"long"}
    assert outputs["long"].name == "transformer_long_5p.pdf"
    with fitz.open(outputs["long"]) as document:
        assert len(document) == 5


def test_long_document_evaluator_resumes_after_committed_batch(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    _make_source(source)
    outputs = build_fixtures(
        source,
        tmp_path / "corpus",
        long_pages=5,
        only={"long"},
    )

    report = evaluate_long_document(
        outputs["long"],
        batch_pages=2,
        interrupt_after_pages=2,
        expected_pages=5,
        max_elapsed_seconds=30,
        max_python_peak_memory_mb=128,
    )

    assert report["status"] == "passed"
    assert report["metrics"]["interrupted_after_pages"] == 2
    assert report["metrics"]["first_resumed_page"] == 4
    assert report["metrics"]["checkpoint_batches"] == 3
