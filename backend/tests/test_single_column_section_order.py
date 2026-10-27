import fitz

from app.parsers.layout import RawTextBlock, order_page_blocks
from app.parsers.pymupdf_parser import PyMuPDFParser
from app.services.chunking import build_chunk_drafts


def raw(text, box, *, lines=3, weight=100):
    return RawTextBlock(1, box, text, 11, lines, weight)


def test_narrow_heading_is_not_a_column_before_centered_single_column_body():
    body = raw("Introduction body", [105, 90, 500, 160])
    heading = raw("2 Next section", [105, 190, 280, 208], lines=1, weight=14)
    following = raw("Next section body", [105, 230, 500, 300])
    ordered = order_page_blocks([heading, following, body], 612)
    assert ordered == [(body, 0), (heading, 0), (following, 0)]


def test_real_parallel_text_still_uses_left_column_then_right_column():
    left = [raw("left top", [45, 80, 285, 140]), raw("left bottom", [45, 160, 285, 220])]
    right = [raw("right top", [330, 70, 570, 150]),
             raw("right bottom", [330, 170, 570, 230])]
    title = raw("Full-width title", [40, 20, 570, 45], lines=1)
    ordered = order_page_blocks([right[0], left[1], title, right[1], left[0]], 612)
    assert [block.text for block, _ in ordered] == [
        "Full-width title", "left top", "left bottom", "right top", "right bottom"]
    assert [column for _, column in ordered] == [0, 1, 1, 2, 2]


def test_short_right_note_does_not_turn_a_single_column_page_into_two_columns():
    body = raw("Continuous body", [105, 90, 500, 160])
    note = raw("short note", [340, 170, 450, 185], lines=1, weight=10)
    heading = raw("2 Next section", [105, 210, 280, 228], lines=1, weight=14)
    assert [block.text for block, _ in order_page_blocks([heading, note, body], 612)] == [
        "Continuous body", "short note", "2 Next section"]


def test_native_single_column_page_keeps_intro_in_its_actual_section(tmp_path):
    path = tmp_path / "single-column.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((105, 70), "1 Introduction", fontsize=13)
    page.insert_textbox(fitz.Rect(105, 90, 503, 185),
                        "The introduction reports the initial compute configuration. " * 5,
                        fontsize=11)
    page.insert_text((105, 240), "2 Architecture", fontsize=13)
    page.insert_textbox(fitz.Rect(105, 265, 503, 360),
                        "The architecture section describes the network components. " * 5,
                        fontsize=11)
    pdf.save(path)
    pdf.close()
    parsed = PyMuPDFParser().parse(str(path)).to_dict()
    chunks = build_chunk_drafts(parsed)
    introduction = next(chunk for chunk in chunks if "initial compute" in chunk.text)
    architecture = next(chunk for chunk in chunks if "network components" in chunk.text)
    assert introduction.section == "1 Introduction"
    assert architecture.section == "2 Architecture"
    assert introduction.chunk_index < architecture.chunk_index
    assert PyMuPDFParser.version.endswith("layout104")


def test_native_rule_footnote_does_not_interrupt_column_continuation(tmp_path):
    path = tmp_path / "column-note.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(50, 90, 285, 220),
                            "This paragraph establishes parallel column body text. " * 5,
                            fontsize=11)
        page.insert_textbox(fitz.Rect(325, 80, 565, 220),
                            "A continued explanation at the beginning of the right column. " * 5,
                            fontsize=11)
        page.insert_textbox(fitz.Rect(50, 620, 285, 670),
                            "The last left column paragraph learns a representation that",
                            fontsize=11)
        page.draw_line(fitz.Point(50, 675), fitz.Point(108, 675))
        page.insert_textbox(fitz.Rect(50, 680, 285, 724),
                            "5 The hardware measurement uses four processors in this run.",
                            fontsize=9)
        pdf.save(path)
    parsed = PyMuPDFParser().parse(str(path)).to_dict()
    blocks = parsed["pages"][0]["blocks"]
    note = next(b for b in blocks if "hardware measurement" in b["text"])
    left_end = next(b for b in blocks if "last left column" in b["text"])
    right_start = next(b for b in blocks if "continued explanation" in b["text"])
    assert note["type"] == "footnote"
    assert blocks.index(left_end) < blocks.index(right_start) < blocks.index(note)
    drafts = build_chunk_drafts(parsed)
    note_draft = next(d for d in drafts if "hardware measurement" in d.text)
    assert note_draft.block_ids == [note["block_id"]]
    assert note_draft.bbox == note["bbox"] and note_draft.page_number == 1
    assert "脚注" in note_draft.section
    assert "continued explanation" not in note_draft.text
    assert all("hardware measurement" not in d.text for d in drafts if d is not note_draft)


def test_footnote_marking_needs_a_rule_smaller_font_and_matching_column():
    from app.parsers.layout import mark_rule_separated_footnotes

    paragraph = RawTextBlock(1, [50, 90, 285, 250], "Substantial body prose. " * 20, 11, 15, 400)
    note = RawTextBlock(1, [50, 685, 285, 710], "A genuine separated explanatory footnote.",
                        9, 2, 43)
    ordinary = RawTextBlock(1, [50, 690, 285, 715], "A normal font paragraph below a line.",
                            11, 2, 40)
    opposite = RawTextBlock(1, [325, 685, 565, 710], "Small text in the other column.",
                            9, 2, 40)
    blocks = [paragraph, note, ordinary, opposite]
    mark_rule_separated_footnotes(blocks, 612, 792, [[50, 675, 108, 675]], [])
    assert note.is_footnote
    assert not paragraph.is_footnote and not ordinary.is_footnote and not opposite.is_footnote


def test_footnote_marking_excludes_table_and_figure_regions():
    from app.parsers.layout import mark_rule_separated_footnotes

    body = RawTextBlock(1, [50, 100, 565, 300], "Body paragraph. " * 50, 11, 15, 700)
    table_row = RawTextBlock(1, [50, 685, 285, 705], "A small-font numeric table row 2 4 6.",
                             9, 2, 40)
    chart_label = RawTextBlock(1, [325, 685, 565, 705], "A small-font chart annotation here.",
                               9, 2, 40)
    mark_rule_separated_footnotes([body, table_row, chart_label], 612, 792,
                                [[50, 675, 108, 675], [325, 675, 383, 675]],
                                [[45, 650, 290, 725], [320, 650, 570, 725]])
    assert not table_row.is_footnote and not chart_label.is_footnote


def test_no_rule_or_upper_page_small_text_is_not_guessed_to_be_a_footnote():
    from app.parsers.layout import mark_rule_separated_footnotes

    body = RawTextBlock(1, [50, 100, 565, 300], "Body paragraph. " * 50, 11, 15, 700)
    small = RawTextBlock(1, [50, 420, 285, 445], "A small note in the middle of the page.",
                         9, 2, 42)
    mark_rule_separated_footnotes([body, small], 612, 792, [], [])
    assert not small.is_footnote
    mark_rule_separated_footnotes([body, small], 612, 792, [[50, 410, 108, 410]], [])
    assert not small.is_footnote


def test_chart_axis_label_below_a_short_rule_is_not_an_explanatory_footnote():
    from app.parsers.layout import mark_rule_separated_footnotes

    body = RawTextBlock(1, [50, 100, 565, 300], "Body paragraph. " * 50, 11, 15, 700)
    label = RawTextBlock(1, [197.3, 619.28, 333.52, 628.24],
                         "Model scale (# parameters in billions)", 9, 1, 38)
    mark_rule_separated_footnotes([body, label], 612, 792, [[197, 610, 255, 610]], [])
    assert not label.is_footnote


def test_chunking_keeps_old_footnotes_without_new_type_unchanged():
    parsed = {"pages": [{"page_number": 1, "blocks": [
        {"block_id": "p1-b1", "type": "text", "text": "Ordinary legacy body", "bbox": [1, 2, 3, 4]},
        {"block_id": "p1-b2", "type": "text", "text": "Legacy unmarked footnote",
         "bbox": [1, 5, 3, 6]},
    ]}]}
    drafts = build_chunk_drafts(parsed)
    assert drafts[0].block_ids == ["p1-b1", "p1-b2"]
    assert "Legacy unmarked footnote" in drafts[0].text


def test_footnote_chunk_does_not_change_following_body_section():
    parsed = {"pages": [{"page_number": 1, "blocks": [
        {"block_id": "p1-b1", "type": "heading", "text": "2 Method", "bbox": [1, 2, 3, 4]},
        {"block_id": "p1-b2", "type": "footnote", "text": "Independent source note",
         "bbox": [1, 5, 3, 6]},
        {"block_id": "p1-b3", "type": "text", "text": "Method paragraph", "bbox": [1, 7, 3, 8]},
    ]}]}
    drafts = build_chunk_drafts(parsed)
    assert next(d for d in drafts if "Method paragraph" in d.text).section == "2 Method"
    assert next(d for d in drafts if "Independent source note" in d.text).block_ids == ["p1-b2"]
