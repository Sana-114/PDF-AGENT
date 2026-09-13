import fitz

from app.parsers.base import Block
from app.parsers.layout import RawTextBlock, classify_block, extract_first_page_people
from app.parsers.pymupdf_parser import PyMuPDFParser


def _insert_textbox(page, rect, text, *, fontsize=10, fontname="helv") -> None:
    rect = fitz.Rect(rect.x0, rect.y0, rect.x1, max(rect.y1, rect.y0 + fontsize * 2))
    result = page.insert_textbox(rect, text, fontsize=fontsize, fontname=fontname)
    assert result >= 0


def _sample_figure_png() -> bytes:
    source = fitz.open()
    page = source.new_page(width=120, height=70)
    page.draw_rect(fitz.Rect(5, 5, 115, 65), color=(0.1, 0.3, 0.8), fill=(0.8, 0.9, 1))
    page.draw_line((15, 55), (105, 15), color=(0.8, 0.1, 0.1), width=3)
    image = page.get_pixmap(alpha=False).tobytes("png")
    source.close()
    return image


def _save_layout_pdf(path) -> None:
    document = fitz.open()
    document.set_metadata({"title": "Layout-Aware Research Assistant"})
    page = document.new_page(width=595, height=842)

    _insert_textbox(
        page,
        fitz.Rect(60, 35, 535, 70),
        "Layout-Aware Research Assistant",
        fontsize=18,
        fontname="hebo",
    )
    _insert_textbox(page, fitz.Rect(120, 70, 475, 90), "Alice Zhang, Bob Smith", fontsize=11)
    _insert_textbox(
        page,
        fitz.Rect(100, 91, 495, 110),
        "Department of Computer Science, Example University",
        fontsize=9,
    )
    _insert_textbox(page, fitz.Rect(60, 125, 535, 145), "Abstract", fontsize=13, fontname="hebo")
    _insert_textbox(
        page,
        fitz.Rect(60, 148, 535, 190),
        "We present a grounded system that preserves page-level evidence and layout anchors.",
        fontsize=10,
    )
    _insert_textbox(
        page, fitz.Rect(60, 205, 285, 225), "1 Introduction", fontsize=13, fontname="hebo"
    )
    _insert_textbox(
        page,
        fitz.Rect(60, 230, 285, 285),
        "The left column introduces the problem and its evidence constraints.",
        fontsize=10,
    )
    _insert_textbox(
        page,
        fitz.Rect(60, 290, 285, 315),
        "L = sum_i x_i / N",
        fontsize=11,
        fontname="cour",
    )
    _insert_textbox(
        page, fitz.Rect(310, 205, 535, 225), "2 Results", fontsize=13, fontname="hebo"
    )
    _insert_textbox(
        page,
        fitz.Rect(310, 230, 535, 275),
        "The right column reports stable extraction results for the benchmark.",
        fontsize=10,
    )
    _insert_textbox(page, fitz.Rect(310, 292, 535, 312), "Table 1. Accuracy", fontsize=10)

    x_values = [310, 410, 510]
    y_values = [320, 345, 370]
    for x_value in x_values:
        page.draw_line((x_value, y_values[0]), (x_value, y_values[-1]), color=(0, 0, 0))
    for y_value in y_values:
        page.draw_line((x_values[0], y_value), (x_values[-1], y_value), color=(0, 0, 0))
    page.insert_text((320, 338), "Model", fontsize=9)
    page.insert_text((420, 338), "Score", fontsize=9)
    page.insert_text((320, 363), "Ours", fontsize=9)
    page.insert_text((420, 363), "98.0", fontsize=9)

    figure_bbox = fitz.Rect(60, 390, 260, 510)
    page.insert_image(figure_bbox, stream=_sample_figure_png())
    _insert_textbox(page, fitz.Rect(60, 515, 285, 540), "Figure 1. System overview", fontsize=10)

    second = document.new_page(width=595, height=842)
    _insert_textbox(
        second, fitz.Rect(60, 55, 535, 80), "3 References", fontsize=14, fontname="hebo"
    )
    _insert_textbox(
        second,
        fitz.Rect(60, 90, 535, 120),
        "[1] A. Author. A grounded paper. Journal of Tests, 2025.",
        fontsize=10,
    )
    _insert_textbox(
        second,
        fitz.Rect(60, 125, 535, 155),
        "[2] B. Writer. Layout parsing in practice. Test Press, 2026.",
        fontsize=10,
    )
    _insert_textbox(
        second, fitz.Rect(60, 190, 535, 215), "4 Appendix A", fontsize=14, fontname="hebo"
    )
    _insert_textbox(
        second,
        fitz.Rect(60, 220, 535, 260),
        "Additional implementation details are provided here.",
        fontsize=10,
    )
    document.save(path)
    document.close()


def test_layout_parser_extracts_academic_structure(tmp_path) -> None:
    path = tmp_path / "layout-paper.pdf"
    _save_layout_pdf(path)

    parsed = PyMuPDFParser().parse(str(path))
    payload = parsed.to_dict()

    assert parsed.schema_version == "0.2.0"
    assert parsed.title == "Layout-Aware Research Assistant"
    assert parsed.authors == ["Alice Zhang", "Bob Smith"]
    assert parsed.affiliations == ["Department of Computer Science, Example University"]
    assert parsed.abstract and parsed.abstract.startswith("We present a grounded system")
    assert [node.text for node in parsed.outline[:2]] == ["Abstract", "1 Introduction"]
    assert parsed.outline[2].text == "2 Results"
    assert parsed.formulas[0].text == "L = sum_i x_i / N"
    assert len(parsed.tables) == 1
    assert parsed.tables[0].caption == "Table 1. Accuracy"
    assert parsed.tables[0].rows == [["Model", "Score"], ["Ours", "98.0"]]
    assert all(cell.bbox for cell in parsed.tables[0].cells)
    assert len(parsed.figures) == 1
    assert parsed.figures[0].caption == "Figure 1. System overview"
    assert [reference.label for reference in parsed.references] == ["1", "2"]
    assert parsed.appendices[0].text == "4 Appendix A"
    assert payload["outline"][0]["bbox"]
    assert payload["references"][0]["block_ids"]


def test_two_column_reading_order_is_stable(tmp_path) -> None:
    path = tmp_path / "layout-paper.pdf"
    _save_layout_pdf(path)

    parsed = PyMuPDFParser().parse(str(path))
    blocks = parsed.pages[0].blocks
    left = next(block for block in blocks if block.text.startswith("The left column"))
    right = next(block for block in blocks if block.text.startswith("The right column"))

    assert left.column == 1
    assert right.column == 2
    assert left.reading_order < right.reading_order


def test_title_guess_ignores_vertical_arxiv_identifier() -> None:
    blocks = [
        RawTextBlock(
            1,
            [10, 215, 38, 560],
            "arXiv:1706.03762v1 [cs.CL] 12 Jun 2017",
            20,
            1,
            38,
        ),
        RawTextBlock(1, [211, 100, 400, 118], "Attention Is All You Need", 17.2, 1, 25),
        RawTextBlock(1, [284, 337, 329, 349], "Abstract", 12, 1, 8),
    ]

    assert PyMuPDFParser._guess_title_from_blocks(blocks) == "Attention Is All You Need"


def test_people_extraction_uses_geometry_and_splits_combined_author_blocks() -> None:
    blocks = [
        Block("p1-b1", "title", "Attention Is All You Need", [210, 100, 400, 118], 0),
        Block(
            "p1-b2",
            "text",
            "Ashish Vaswani ∗ Google Brain avaswani@google.com",
            [116, 184, 216, 218],
            1,
        ),
        Block(
            "p1-b3",
            "text",
            "Aidan N. Gomez ∗† University of Toronto aidan@cs.toronto.edu",
            [235, 234, 340, 268],
            2,
        ),
        Block("p1-b4", "heading", "Abstract", [284, 337, 329, 349], 3, level=1),
        Block(
            "p1-b5",
            "text",
            "arXiv:1706.03762v1 [cs.CL] 12 Jun 2017",
            [10, 215, 38, 560],
            4,
        ),
    ]

    authors, affiliations = extract_first_page_people(blocks)

    assert authors == ["Ashish Vaswani", "Aidan N. Gomez"]
    assert affiliations == [
        "Google Brain avaswani@google.com",
        "University of Toronto aidan@cs.toronto.edu",
    ]


def test_heading_classifier_rejects_vertical_stamp_and_small_numbered_footnote() -> None:
    stamp = RawTextBlock(
        1,
        [10, 215, 38, 560],
        "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023",
        20,
        1,
        38,
    )
    footnote = RawTextBlock(
        8,
        [110, 700, 500, 720],
        "5 We used values of 2.8, 3.7, 6.0 and 9.5 TFLOPS for K80 and P100.",
        8,
        1,
        74,
    )

    assert classify_block(stamp, body_size=10, title=None) == ("text", None)
    assert classify_block(footnote, body_size=10, title=None) == ("text", None)
