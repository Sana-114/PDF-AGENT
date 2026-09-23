import fitz

from app.parsers.base import Block, Page
from app.parsers.layout import (
    RawTextBlock,
    extract_references,
    extract_text_blocks,
    make_figure_nodes,
    nearest_caption,
)
from app.parsers.people import extract_front_matter_people
from app.parsers.pymupdf_parser import PyMuPDFParser


def _block(text, y, kind="text", page=1, x=60, right=500):
    return Block(f"p{page}-{y}", kind, text, [x, y, right, y + 12], y)


def _raw(text, y, lines=None, page=1):
    return RawTextBlock(page, [60, y, 500, y + 15], text, 10, 1, len(text), lines or [])


def test_front_matter_splits_names_not_only_starred_authors():
    authors, affiliations = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw("Alice Smith*, Bob Jones, Carol Lee†", 90),
                _raw("Microsoft Research", 120),
                _raw("Abstract", 150),
                _raw("Body Writer", 170),
            ]
        ],
        "Test Paper",
    )
    assert authors == ["Alice Smith", "Bob Jones", "Carol Lee"]
    assert affiliations == ["Microsoft Research"]


def test_unlabelled_abstract_stops_author_extraction():
    authors, _ = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw("Alice Smith1, Bob Jones2", 90),
                _raw("Our method studies sequence models. " * 20, 120),
                _raw("Model Head", 400),
            ]
        ],
        "Test Paper",
    )
    assert authors == ["Alice Smith", "Bob Jones"]


def test_email_aligned_author_rows_exclude_postal_address_and_keep_accented_name():
    lines = [
        {"text": "Alice Smith", "bbox": [60, 100, 140, 116]},
        {"text": "alice@example.org", "bbox": [300, 101, 440, 115]},
        {"text": "New York", "bbox": [60, 125, 140, 139]},
        {"text": "´Edouard Duchesnay", "bbox": [60, 150, 180, 168]},
        {"text": "edouard@example.org", "bbox": [300, 154, 440, 167]},
    ]
    authors, _ = extract_front_matter_people(
        [
            [
                _raw("Test Paper", 50),
                _raw("Author metadata @", 100, lines),
                _raw("Abstract", 200),
            ]
        ],
        "Test Paper",
    )
    assert authors == ["Alice Smith", "´Edouard Duchesnay"]


def test_native_grid_keeps_independent_names_and_organization(tmp_path):
    path = tmp_path / "grid.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 70), "A Grid Paper", fontsize=18)
        page.insert_text((60, 110), "Alice Smith", fontsize=11)
        page.insert_text((250, 110), "Bob Jones", fontsize=11)
        page.insert_text((60, 130), "UC Berkeley", fontsize=10)
        page.insert_text((60, 170), "Abstract", fontsize=13)
        doc.save(path)
    parsed = PyMuPDFParser().parse(str(path))
    assert parsed.authors == ["Alice Smith", "Bob Jones"]
    assert parsed.affiliations == ["UC Berkeley"]


def test_merged_reference_column_splits_with_exact_line_boxes():
    texts = [
        "References",
        "1.",
        "Alice Smith. First study (2020).",
        "2.",
        "Bob Jones. Second study (2021).",
        "Acknowledgements",
        "We thank colleagues.",
    ]
    lines = [
        {"bbox": [60, 100 + i * 15, 280, 112 + i * 15], "spans": [{"text": text, "size": 10}]}
        for i, text in enumerate(texts)
    ]
    raw = extract_text_blocks(
        {"blocks": [{"type": 0, "bbox": [60, 100, 280, 210], "lines": lines}]}, 5
    )
    assert [b.text for b in raw] == [
        "References",
        "1. Alice Smith. First study (2020).",
        "2. Bob Jones. Second study (2021).",
        "Acknowledgements",
        "We thank colleagues.",
    ]
    assert raw[1].bbox == [60, 115, 280, 142]
    pages = PyMuPDFParser._build_pages([raw], [(600, 800)], body_size=10, title=None)
    refs = extract_references(pages)
    assert [r.label for r in refs] == ["1", "2"]
    assert "thank" not in refs[-1].text


def test_alphabetic_bibkeys_and_cross_page_anchor():
    first = Page(
        1,
        600,
        800,
        [
            _block("References", 650, "heading"),
            _block("[AB+20] Alice Smith. A study with a long", 680),
        ],
    )
    second = Page(
        2,
        600,
        800,
        [
            _block("continued title. Test Journal, 2020.", 80, page=2),
            _block("[CD21] Carol Lee. Another paper. 2021.", 110, page=2),
        ],
    )
    refs = extract_references([first, second])
    assert [r.label for r in refs] == ["AB+20", "CD21"]
    assert "continued title" in refs[0].text
    assert refs[0].page_number == 1
    assert refs[0].bbox == [60, 680, 500, 692]
    assert refs[0].block_ids == ["p1-680", "p2-80"]


def test_author_year_entries_do_not_invent_numeric_citations_or_absorb_next_page():
    page = Page(
        1,
        600,
        800,
        [
            _block("References", 600, "heading"),
            _block("Alice Smith and Bob Jones. Grounded models. Journal of Tests, 2020.", 630),
            _block("Carol Lee. Another grounded study. Conference on Tests, 2021.", 680),
        ],
    )
    next_page = Page(
        2,
        600,
        800,
        [
            _block("Models Dataset Epochs Learning Rate", 80, page=2),
            _block("A ADDITIONAL DETAILS", 200, page=2),
            _block("Alice Smith. This is body prose referring to 2020.", 230, page=2),
        ],
    )
    refs = extract_references([page, next_page])
    assert [r.label for r in refs] == ["author-year-1", "author-year-2"]
    assert refs[-1].text.endswith("2021.")


def test_single_column_references_restore_y_order_before_detecting_boundary():
    page = Page(
        1,
        600,
        800,
        [
            _block("References", 600, "heading", right=200),
            _block("Alice Smith. A grounded study with substantial details. " * 2 + "2020.", 640),
            _block("Earlier body prose with a date 2019. " * 4, 500),
        ],
    )
    refs = extract_references([page])
    assert len(refs) == 1
    assert "Earlier" not in refs[0].text


def test_caption_matching_does_not_cross_columns():
    left = _block("Figure 1. Left", 200, "figure_caption", right=280)
    right = _block("Figure 2. Right", 190, "figure_caption", x=320, right=550)
    assert nearest_caption([left, right], [330, 100, 500, 185], "figure_caption") == right
    assert nearest_caption([left], [330, 100, 500, 185], "figure_caption") is None


def test_captioned_tile_grid_becomes_one_figure():
    infos = [
        {"bbox": [60 + x * 32, 80 + y * 32, 80 + x * 32, 100 + y * 32], "xref": 1}
        for x in range(8)
        for y in range(12)
    ]
    figures = make_figure_nodes(
        infos,
        page_number=1,
        page_area=600 * 800,
        blocks=[_block("Figure 1. Attention maps", 465, "figure_caption")],
    )
    assert len(figures) == 1
    assert figures[0].bbox == [60, 80, 304, 452]
    assert figures[0].xref is None


def test_vector_detection_requires_caption_and_rejects_table_grid(tmp_path):
    path = tmp_path / "vector.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.draw_rect((60, 100, 260, 200))
        page.insert_text((60, 225), "Figure 1. Vector architecture")
        page.draw_rect((320, 100, 520, 200))
        page.insert_text((320, 225), "Table 1. Scores")
        doc.save(path)
    parsed = PyMuPDFParser().parse(str(path))
    assert len(parsed.figures) == 1
    assert parsed.figures[0].caption == "Figure 1. Vector architecture"
    assert parsed.figures[0].xref is None


def test_front_matter_lines_are_not_retained_for_whole_books():
    page = {
        "blocks": [
            {
                "type": 0,
                "bbox": [60, 100, 200, 112],
                "lines": [{"spans": [{"text": "Body text", "size": 10}]}],
            }
        ]
    }
    assert extract_text_blocks(page, 1)[0].lines
    assert extract_text_blocks(page, 4)[0].lines == []
