import fitz

from app.parsers.base import Block, Page
from app.parsers.layout import RawTextBlock, extract_references
from app.parsers.people import enrich_people_geometry, extract_front_matter_people
from app.parsers.pymupdf_parser import PyMuPDFParser


def raw(text, y, lines=None):
    return RawTextBlock(1, [60, y, 500, y + 12], text, 10, 1, len(text), lines or [])


def people(text):
    return extract_front_matter_people([[raw("Example Paper", 60), raw(text, 90),
                                        raw("Abstract", 150)]], "Example Paper")[0]


def test_unicode_credit_markers_and_collective_at_sign():
    assert people("Aohan Zeng⋄†∗, Weng Lam Tam⋄§") == ["Aohan Zeng", "Weng Lam Tam"]
    assert people("OpenAI∗") == ["OpenAI"]
    assert people("Llama Team, AI @ Meta1") == ["Llama Team, AI @ Meta"]
    assert people("Date: July 23, 2024") == []
    assert people("Microsoft Research") == []


def test_marker_separated_full_names_not_suffix_or_merged_names():
    assert people("Daixuan Cheng†* Yuxian Gu‡ Shaohan Huang†B Junyu Bi† "
                  "Minlie Huang‡B Furu Wei†") == ["Daixuan Cheng", "Yuxian Gu",
                    "Shaohan Huang", "Junyu Bi", "Minlie Huang", "Furu Wei"]
    assert people("Henrique Ponde de Oliveira Pinto * 1 Diego de las Casas 2") == [
        "Henrique Ponde de Oliveira Pinto", "Diego de las Casas"]


def test_body_emails_and_organization_mentions_do_not_filter_authors():
    lines = [{"text": "alice@example.org", "bbox": [300, 230, 480, 242]},
             {"text": "Google Research", "bbox": [60, 230, 240, 242]},
             {"text": "bob@example.org", "bbox": [300, 250, 480, 262]},
             {"text": "Unrelated Body Writer", "bbox": [60, 250, 240, 262]}]
    a, _ = extract_front_matter_people([[raw("Example Paper", 60),
        raw("Alice Smith, Bob Jones", 90), raw("Abstract", 150),
        raw("emails in body", 230, lines)]], "Example Paper")
    assert a == ["Alice Smith", "Bob Jones"]


def test_native_gap_geometry_restores_names_without_changing_source_text():
    chars = [{"c": c, "bbox": [i * 5, 90, i * 5 + 5, 100]}
             for i, c in enumerate("Peter Albert Amjad Almahairi")]
    # A source inter-author space, wider than ordinary within-name spaces.
    chars[12]["bbox"][2] += 4
    for char in chars[13:]:
        char["bbox"][0] += 4
        char["bbox"][2] += 4
    line = {"text": "Peter Albert Amjad Almahairi", "bbox": [0, 90, 139, 100]}
    block = raw(line["text"], 90, [line])
    enrich_people_geometry([block], {"blocks": [{"lines": [{"bbox": line["bbox"],
        "dir": [1, 0], "spans": [{"size": 14, "chars": chars}]}]}]})
    assert line["people_parts"] == ["Peter Albert", "Amjad Almahairi"]
    assert block.text == "Peter Albert Amjad Almahairi"


def test_accent_and_anonymous_web_references_keep_original_anchors():
    texts = ["References",
             "Mart´ın Abadi and Paul Barham. TensorFlow: Large-scale learning, 2016.",
             "CWE-78: OS command injection. https://cwe.mitre.org/data/definitions/78.html, 2006.",
             "AI@Meta. Llama 3.1 model card. https: //github.com/meta-llama, 2024.",
             "Appendix A", "A Body Writer. Not a bibliography entry. 2024."]
    blocks = [Block(f"p1-b{i}", "heading" if i in {0, 4} else "text", t,
                    [60, 90 + i * 40, 500, 110 + i * 40], i)
              for i, t in enumerate(texts)]
    refs = extract_references([Page(1, 600, 800, blocks)])
    assert len(refs) == 3
    assert [r.text for r in refs] == texts[1:4]
    assert [r.block_ids for r in refs] == [["p1-b1"], ["p1-b2"], ["p1-b3"]]


def test_captioned_chart_paths_are_excluded_but_table_paths_stay():
    with fitz.open() as doc:
        page = doc.new_page()
        page.draw_rect(fitz.Rect(60, 60, 140, 140))
        page.draw_rect(fitz.Rect(250, 60, 400, 140))
        all_paths = PyMuPDFParser._table_paths(page)
        filtered = PyMuPDFParser._table_paths(page, figure_boxes=[[50, 50, 150, 150]])
        assert len(all_paths) == 2 and len(filtered) == 1
        assert filtered[0]["rect"] == fitz.Rect(250, 60, 400, 140)


def test_compact_table_paths_match_native_grid_cells(tmp_path):
    with fitz.open() as doc:
        page = doc.new_page()
        for x in [60, 160, 260]:
            page.draw_line((x, 100), (x, 180))
        for y in [100, 140, 180]:
            page.draw_line((60, y), (260, y))
        for x, y, text in [(70, 120, "Model"), (170, 120, "Score"),
                           (70, 160, "Ours"), (170, 160, "98")]:
            page.insert_text((x, y), text)
        native = [t.extract() for t in page.find_tables().tables]
        compact = [t.extract() for t in page.find_tables(
            paths=PyMuPDFParser._table_paths(page)).tables]
        assert native == compact == [[["Model", "Score"], ["Ours", "98"]]]


class _ClippedPage:
    rect = fitz.Rect(0, 0, 600, 800)
    number = 0

    def cluster_drawings(self, *, drawings):
        with fitz.open() as document:
            return document.new_page(width=600, height=800).cluster_drawings(drawings=drawings)

    def get_cdrawings(self, callback, *, extended=False):
        records = [
            {"type": "clip", "level": 0, "scissor": (40, 150, 350, 260)},
            {"type": "clip", "level": 1, "scissor": (60, 160, 300, 250)},
            {"type": "fs", "level": 2, "rect": (80, 170, 100, 700),
             "items": [("re", (80, 170, 100, 700), 1)], "closePath": False,
             "width": 1, "color": (0, 0, 0), "fill": (0, 0, 1)},
            {"type": "s", "level": 0, "rect": (400, 100, 450, 180),
             "items": [("re", (400, 100, 450, 180), 1)], "closePath": False,
             "width": 1, "color": (0, 0, 0), "fill": None},
        ]
        for record in records:
            if extended or record["type"] not in {"clip", "group"}:
                callback(record)


def test_native_table_paths_honor_nested_clips_and_restore_sibling_scope():
    paths = PyMuPDFParser._table_paths(_ClippedPage())
    assert paths[0]["rect"] == fitz.Rect(80, 170, 100, 250)
    assert paths[1]["rect"] == fitz.Rect(400, 100, 450, 180)
    # The clip boundary is NOT a drawn rectangle edge: don't fabricate a bottom rule.
    assert all(item[0] == "l" for item in paths[0]["items"])
    assert not any(a.y == b.y == 250 for _, a, b in paths[0]["items"])


def test_clipped_bar_chart_does_not_expand_to_the_hidden_bar_bottom():
    captions = [RawTextBlock(1, [50, 280, 340, 294], "Figure 1: Trend", 10, 1, 20)]
    regions = PyMuPDFParser._extract_vector_info(_ClippedPage(), captions, [])
    chart = next(r for r in regions if r["bbox"][0] < 150)
    assert chart["bbox"][3] <= 250
    assert chart["chart_like"]
    assert chart["clipped_chart_like"]


def test_clipping_preserves_visible_horizontal_rules_with_zero_height():
    assert PyMuPDFParser._clip_line((20, 100), (180, 100), fitz.Rect(40, 80, 120, 150)) == (
        fitz.Point(40, 100), fitz.Point(120, 100),
    )
    assert PyMuPDFParser._clip_line((20, 70), (180, 70), fitz.Rect(40, 80, 120, 150)) is None


def test_clipped_chart_and_neighbor_table_keep_distinct_caption_ownership():
    from app.parsers.layout import make_figure_nodes

    table = Block("t", "table_caption", "Table 1: Scores", [50, 140, 350, 154], 0)
    figure = Block("f", "figure_caption", "Figure 1: Trend", [50, 280, 350, 294], 1)
    grid = {"bbox": [50, 160, 350, 250], "vector": True}
    chart = {**grid, "chart_like": True, "clipped_chart_like": True}
    assert make_figure_nodes([grid], page_number=1, page_area=480000, blocks=[table, figure]) == []
    assert make_figure_nodes([{**grid, "chart_like": True}], page_number=1,
                             page_area=480000, blocks=[table, figure]) == []
    nodes = make_figure_nodes([chart], page_number=1, page_area=480000, blocks=[table, figure])
    assert len(nodes) == 1 and nodes[0].caption_block_id == "f"


def test_empty_clip_hides_paths_but_an_unclipped_horizontal_rule_remains():
    class EmptyClipPage(_ClippedPage):
        def get_cdrawings(self, callback, *, extended=False):
            callback({"type": "clip", "level": 0, "scissor": (0, 100, 600, 100)})
            callback({"type": "s", "level": 1, "rect": (60, 100, 160, 100),
                      "items": [("l", (60, 100), (160, 100))]})
            callback({"type": "s", "level": 0, "rect": (60, 200, 160, 200),
                      "items": [("l", (60, 200), (160, 200))], "closePath": False})

    paths = PyMuPDFParser._table_paths(EmptyClipPage())
    assert len(paths) == 1 and paths[0]["rect"] == fitz.Rect(60, 200, 160, 200)


def test_figure_body_verb_is_not_an_extra_caption_but_colon_caption_remains():
    from app.parsers.layout import is_figure_caption

    assert not is_figure_caption("Figure 2 decomposes the benchmark into task groups.")
    assert is_figure_caption("Figure 2: Breakdown of the benchmark into task groups.")
