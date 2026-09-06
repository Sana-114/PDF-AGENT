import fitz

from app.parsers import PdfContentKind, get_parser, inspect_pdf


def _save_native_pdf(path, page_count: int = 2) -> None:
    document = fitz.open()
    for index in range(page_count):
        page = document.new_page(width=400, height=400)
        page.insert_text(
            (30, 60),
            f"Page {index + 1}: this is a native PDF page with enough searchable text content.",
        )
    document.save(path)
    document.close()


def _save_scanned_pdf(path, page_count: int = 2) -> None:
    source = fitz.open()
    source_page = source.new_page(width=400, height=400)
    source_page.insert_text(
        (30, 60), "This sentence is rasterized and must not remain in the PDF text layer."
    )
    image = source_page.get_pixmap().tobytes("png")

    scanned = fitz.open()
    for _ in range(page_count):
        page = scanned.new_page(width=400, height=400)
        page.insert_image(page.rect, stream=image)
    scanned.save(path)
    scanned.close()
    source.close()


def test_inspect_pdf_detects_native_text(tmp_path) -> None:
    path = tmp_path / "native.pdf"
    _save_native_pdf(path)

    diagnostics = inspect_pdf(str(path))

    assert diagnostics.content_kind == PdfContentKind.NATIVE_TEXT
    assert diagnostics.native_text_ratio == 1.0
    assert diagnostics.text_pages == [1, 2]


def test_inspect_pdf_detects_scanned_images(tmp_path) -> None:
    path = tmp_path / "scanned.pdf"
    _save_scanned_pdf(path)

    diagnostics = inspect_pdf(str(path))
    parser = get_parser(str(path))
    parsed = parser.parse(str(path))

    assert diagnostics.content_kind == PdfContentKind.SCANNED_IMAGE
    assert diagnostics.image_pages == [1, 2]
    assert any("OCR" in warning for warning in parsed.warnings)


def test_inspect_pdf_detects_hybrid_documents(tmp_path) -> None:
    native_path = tmp_path / "native.pdf"
    scanned_path = tmp_path / "scanned.pdf"
    hybrid_path = tmp_path / "hybrid.pdf"
    _save_native_pdf(native_path, page_count=1)
    _save_scanned_pdf(scanned_path, page_count=1)

    hybrid = fitz.open()
    with fitz.open(native_path) as native:
        hybrid.insert_pdf(native)
    with fitz.open(scanned_path) as scanned:
        hybrid.insert_pdf(scanned)
    hybrid.save(hybrid_path)
    hybrid.close()

    diagnostics = inspect_pdf(str(hybrid_path))

    assert diagnostics.content_kind == PdfContentKind.HYBRID
    assert diagnostics.text_pages == [1]
    assert diagnostics.low_text_pages == [2]

