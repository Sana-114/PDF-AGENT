import fitz

from app.parsers import SelectiveOcrParser, get_parser
from app.parsers.layout import extract_text_blocks
from app.parsers.raster_layout import detect_raster_layout


def _save_layout_source(path) -> None:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((60, 72), "Scanned Layout Benchmark", fontsize=18)
    page.insert_text((60, 108), "Table 1. Evaluation results", fontsize=11)

    x_values = [60, 250, 450]
    y_values = [125, 165, 205]
    for x_value in x_values:
        page.draw_line((x_value, y_values[0]), (x_value, y_values[-1]), width=1.2)
    for y_value in y_values:
        page.draw_line((x_values[0], y_value), (x_values[-1], y_value), width=1.2)
    page.insert_text((75, 151), "Model", fontsize=11)
    page.insert_text((270, 151), "Score", fontsize=11)
    page.insert_text((75, 191), "Ours", fontsize=11)
    page.insert_text((270, 191), "98.0", fontsize=11)

    page.draw_rect(fitz.Rect(75, 285, 455, 505), width=1.4)
    page.draw_line((95, 475), (430, 320), color=(0.1, 0.3, 0.8), width=4)
    page.draw_circle((180, 420), 28, color=(0.8, 0.2, 0.1), width=3)
    page.insert_text((75, 530), "Figure 1. System accuracy trend", fontsize=11)
    document.save(path)
    document.close()


def _save_raster_copy(source_path, target_path) -> None:
    with fitz.open(source_path) as source:
        image = source[0].get_pixmap(dpi=144, alpha=False).tobytes("png")
    scanned = fitz.open()
    page = scanned.new_page(width=600, height=800)
    page.insert_image(page.rect, stream=image)
    scanned.save(target_path)
    scanned.close()


def test_caption_guided_detector_recovers_grid_and_figure_bbox(tmp_path) -> None:
    path = tmp_path / "layout-source.pdf"
    _save_layout_source(path)

    with fitz.open(path) as document:
        page = document[0]
        textpage = page.get_textpage()
        raw_blocks = extract_text_blocks(page.get_text("dict", textpage=textpage), 1)
        tables, figures = detect_raster_layout(
            page,
            textpage=textpage,
            raw_blocks=raw_blocks,
        )

    assert len(tables) == 1
    assert tables[0].column_count == 2
    assert tables[0].rows == [["Model", "Score"], ["Ours", "98.0"]]
    assert len(tables[0].cells) == 4
    assert len(figures) == 1
    assert figures[0]["source"] == "raster_caption_region"
    assert figures[0]["bbox"][1] < 300
    assert figures[0]["bbox"][3] <= 530


def test_selective_ocr_parser_exposes_scanned_table_and_figure(tmp_path) -> None:
    source_path = tmp_path / "layout-source.pdf"
    scan_path = tmp_path / "layout-scan.pdf"
    _save_layout_source(source_path)
    _save_raster_copy(source_path, scan_path)

    parser = get_parser(str(scan_path))
    parsed = parser.parse(str(scan_path))

    assert isinstance(parser, SelectiveOcrParser)
    assert parser.ocr_pages == [1]
    assert parser.raster_table_pages == [1]
    assert parser.raster_figure_pages == [1]
    assert len(parsed.tables) == 1
    assert parsed.tables[0].caption
    assert parsed.tables[0].caption.casefold().startswith("table 1")
    assert parsed.tables[0].markdown.startswith("| Model | Score |")
    assert len(parsed.figures) == 1
    assert parsed.figures[0].caption
    assert parsed.figures[0].caption.casefold().startswith("figure 1")
    assert parsed.figures[0].xref is None
    assert any("扫描版式恢复" in warning for warning in parsed.warnings)
