import fitz
import pytest

from app.parsers.front_matter_ocr import (
    author_grid_region,
    compatible_people,
    project_crop_blocks,
)
from app.parsers.layout import RawTextBlock
from app.parsers.registry import get_parser


def _block(text, bbox=(100, 100, 500, 200), lines=None):
    return RawTextBlock(1, list(bbox), text, 10, len(lines or [text]), len(text), lines or [])


def _front_matter(first="Marek Luczak\"", second="Anna Martin*"):
    lines = [{"text": first, "bbox": [110, 110, 200, 120]},
             {"text": second, "bbox": [300, 110, 400, 120]},
             {"text": "Google Brain", "bbox": [110, 125, 200, 135]},
             {"text": "marek@example.org", "bbox": [100, 140, 215, 150]},
             {"text": "anna@example.org", "bbox": [300, 140, 415, 150]}]
    return [_block("Example Research Paper", bbox=(100, 60, 500, 80)),
            _block(" ".join(line["text"] for line in lines), lines=lines),
            _block("Abstract", bbox=(100, 210, 500, 230))]


def test_compatible_source_spelling_can_change_diacritics_not_people():
    original = _front_matter()
    candidate = _front_matter(first="Marek Łuczak*")
    assert compatible_people(original, candidate, "Example Research Paper")
    assert not compatible_people(original, _front_matter(first="Alice Brown*"),
                                 "Example Research Paper")
    assert not compatible_people(original, _front_matter(first="Anna Martin*",
                                                       second="Marek Łuczak*"),
                                 "Example Research Paper")


def test_crop_cannot_remove_affiliation():
    original = _front_matter()
    candidate = _front_matter(first="Marek Łuczak*")
    candidate[1].lines = [line for line in candidate[1].lines if line["text"] != "Google Brain"]
    candidate[1].text = " ".join(line["text"] for line in candidate[1].lines)
    assert not compatible_people(original, candidate, "Example Research Paper")


def test_crop_is_caption_grid_gated_and_bounded_not_margin_noise():
    block = _front_matter(first="Marek Luczak*")[1]
    # A merged vertical sidebar should not become part of the author crop.
    block.bbox[0] = 10
    block.lines.append({"text": "arxiv side noise", "bbox": [10, 90, 25, 205]})
    region = author_grid_region(block, fitz.Rect(0, 0, 600, 800))
    assert region == fitz.Rect(94, 104, 421, 156)
    assert author_grid_region(_block("ordinary body text"), fitz.Rect(0, 0, 600, 800)) is None
    assert author_grid_region(block, fitz.Rect(0, 0, 600, 200)) is None


def test_crop_projection_preserves_source_line_and_page_anchor():
    source = _block("Marek Łuczak*", bbox=(10, 20, 110, 30),
                    lines=[{"text": "Marek Łuczak*", "bbox": [10, 20, 110, 30]}])
    projected = project_crop_blocks([source], origin=(100, 200), scale=(2, 2))[0]
    assert projected.bbox == [120, 240, 320, 260]
    assert projected.lines[0]["bbox"] == projected.bbox
    assert projected.lines[0]["text"] == source.lines[0]["text"]
    assert source.bbox == [10, 20, 110, 30]


def test_failed_optional_crop_preserves_original_result(tmp_path, monkeypatch):
    from app.core.config import settings

    path = tmp_path / "native.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((100, 72), "Native paper with enough text for routing detection")
        document.save(path)
    # A pure native parse must not use the optional OCR refinement at all.
    parser = get_parser(str(path))
    with fitz.open(path) as document:
        raw = _front_matter(first="Marek Luczak*")
        assert parser.parser._refine_text_blocks(document[0], textpage=None,
                                                raw_blocks=raw, warnings=[]) is raw
        from app.parsers.ocr_parser import SelectiveOcrParser

        ocr = SelectiveOcrParser(parser.diagnostics, languages="eng", dpi=300,
                                 min_text_chars=32, tessdata=settings.ocr_tessdata,
                                 front_matter_languages="missing_language")
        monkeypatch.setattr(fitz.Pixmap, "pdfocr_tobytes",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("missing")))
        warnings = []
        result = ocr._refine_text_blocks(document[0], textpage=document[0].get_textpage(),
                                        raw_blocks=raw, warnings=warnings)
        assert result is raw
        assert "保留页级原始" in warnings[0]
        assert ocr.front_matter_refinements == []


def test_crop_audit_restored_in_parser_checkpoint(tmp_path):
    from app.parsers.diagnostics import inspect_pdf
    from app.parsers.ocr_parser import SelectiveOcrParser

    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(path)
    parser = SelectiveOcrParser(inspect_pdf(str(path)), languages="eng", dpi=300,
                               min_text_chars=32)
    record = {"original_text": "Marek Luczak\"", "refined_text": "Marek Łuczak*"}
    parser.front_matter_refinements = [record]
    metadata = parser._checkpoint_metadata()
    parser.front_matter_refinements = []
    parser._restore_checkpoint_metadata(metadata)
    assert parser.processing_metadata()["front_matter_refinements"] == [record]


@pytest.mark.parametrize("field,value", [("dpi", 450), ("front_matter_languages", "eng+pol")])
def test_parser_revision_changes_with_ocr_configuration(tmp_path, field, value):
    from app.parsers.diagnostics import inspect_pdf
    from app.parsers.ocr_parser import SelectiveOcrParser

    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(path)
    kwargs = {"languages": "eng", "dpi": 300, "min_text_chars": 32}
    baseline = SelectiveOcrParser(inspect_pdf(str(path)), **kwargs)
    changed = SelectiveOcrParser(inspect_pdf(str(path)), **{**kwargs, field: value})
    assert baseline.version != changed.version
