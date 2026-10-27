import fitz

from app.parsers.layout import RawTextBlock, TableSnapshot, extract_text_blocks
from app.parsers.text_table import (
    PositionedWord,
    _assign_caption_grids,
    _split_cells,
    extract_borderless_tables,
)


def _rows(page, start_y):
    for y, label, value in [
        (start_y, "Method", "Score"),
        (start_y + 22, "Alpha", "7.5"),
        (start_y + 44, "Beta", "8.2"),
    ]:
        page.insert_text((60, y), label, fontsize=10)
        page.insert_text((240, y), value, fontsize=10)


def test_bottom_caption_recovers_the_table_above_not_the_body_below():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        _rows(page, 90)
        page.insert_text((60, 164), "Table 1: Accuracy results", fontsize=10)
        page.insert_text((60, 190), "The experiment was conducted on held-out examples.",
                         fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw)
        assert len(tables) == 1
        assert tables[0].rows == [["Method", "Score"], ["Alpha", "7.5"], ["Beta", "8.2"]]
        assert tables[0].bbox[3] < 150


def test_paragraph_immediately_after_the_grid_is_not_a_partial_table_row():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 60), "Table 1: Accuracy results", fontsize=10)
        _rows(page, 90)
        page.insert_text((60, 151),
                         "The experiment was conducted on held-out examples with repeated runs.",
                         fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw)
        assert len(tables) == 1
        assert len(tables[0].rows) == 3
        assert tables[0].bbox[3] < 145


def test_caption_supported_chart_below_a_table_is_not_recovered_as_a_second_table():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        _rows(page, 90)
        page.insert_text((60, 164), "Table 1: Accuracy results", fontsize=10)
        _rows(page, 195)
        page.insert_text((60, 275), "Figure 1: Accuracy trend", fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw, excluded_boxes=[[50, 180, 330, 260]])
        assert len(tables) == 1
        assert tables[0].bbox[3] < 150


def test_bottom_caption_prefers_the_closer_grid_without_vector_detection():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        _rows(page, 90)
        page.insert_text((60, 155), "Table 1: Accuracy results", fontsize=10)
        _rows(page, 220)
        page.insert_text((60, 290), "Figure 1: Accuracy trend", fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw)
        assert len(tables) == 1
        assert tables[0].bbox[3] < 150


def test_short_wrapped_cell_line_remains_part_of_the_table():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 60), "Table 1: Accuracy results", fontsize=10)
        _rows(page, 90)
        page.insert_text((60, 146), "(variant)", fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw)
        assert len(tables) == 1
        assert tables[0].rows[-1] == ["(variant)", ""]


def test_centered_wrapped_header_in_a_middle_column_does_not_end_the_grid():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 60), "Table 1: Accuracy results", fontsize=10)
        for x, text in [(60, "Method"), (200, "Sequential"), (320, "Path")]:
            page.insert_text((x, 90), text, fontsize=10)
        page.insert_text((197, 101), "Operations", fontsize=10)
        for y, label in [(125, "Alpha"), (150, "Beta")]:
            for x, text in [(60, label), (215, "O(1)"), (330, "O(n)")]:
                page.insert_text((x, y), text, fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw)
        assert len(tables) == 1
        assert tables[0].rows[1] == ["", "Operations", ""]
        assert tables[0].rows[-1] == ["Beta", "O(1)", "O(n)"]


def test_spanning_group_header_followed_by_data_does_not_end_the_grid():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 60), "Table 1: Accuracy results", fontsize=10)
        for y, label, value in [(90, "Method", "Score"), (125, "Alpha", "7.5"),
                                (150, "Beta", "8.2")]:
            page.insert_text((60, y), label, fontsize=10)
            page.insert_text((170, y), value, fontsize=10)
        page.insert_text((60, 107), "Top leaderboard systems (evaluation date)", fontsize=10)
        raw = extract_text_blocks(page.get_text("dict"), 1)
        tables = extract_borderless_tables(page, raw)
        assert len(tables) == 1
        assert len(tables[0].rows) == 4
        assert tables[0].rows[-1] == ["Beta", "8.2"]


def test_compact_decimal_columns_do_not_merge_into_one_score():
    row = [PositionedWord([60, 20, 128, 30], "Long model"),
           PositionedWord([130, 20, 149, 30], "12.3"),
           PositionedWord([152, 20, 171, 30], "45.6"),
           PositionedWord([174, 20, 193, 30], "78.9")]
    assert [cell.text for cell in _split_cells(row)] == ["Long model", "12.3", "45.6", "78.9"]


def test_spaced_thousands_remain_one_value_not_two_columns():
    row = [PositionedWord([60, 20, 100, 30], "Baseline"),
           PositionedWord([150, 20, 155, 30], "2"),
           PositionedWord([157, 20, 172, 30], "500"),
           PositionedWord([190, 20, 209, 30], "12.3")]
    assert [cell.text for cell in _split_cells(row)] == ["Baseline", "2 500", "12.3"]


def test_narrative_numbers_and_exponents_do_not_gain_dense_columns():
    row = [PositionedWord([60, 20, 82, 30], "With"),
           PositionedWord([85, 20, 104, 30], "12.3"),
           PositionedWord([107, 20, 127, 30], "trials"),
           PositionedWord([130, 20, 149, 30], "45.6")]
    assert len(_split_cells(row)) == 1
    exponent = [PositionedWord([60, 20, 70, 30], "10"),
                PositionedWord([70, 16, 77, 24], "20")]
    assert len(_split_cells(exponent)) == 1


def test_nearby_bottom_captions_keep_their_own_dense_grids():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        for start_y, label, scores in [(80, "First", ("12.3", "45.6")),
                                       (210, "Second", ("67.8", "90.1"))]:
            for x, text in [(60, "Model"), (180, "EM"), (204, "F1")]:
                page.insert_text((x, start_y), text, fontsize=10)
            for y, name in [(start_y + 22, label), (start_y + 44, "Extended variant")]:
                for x, text in [(60, name), (180, scores[0]), (204, scores[1])]:
                    page.insert_text((x, y), text, fontsize=10)
        page.insert_text((60, 160), "Table 1: First benchmark results", fontsize=10)
        page.insert_text((60, 282), "Table 2: Second benchmark results", fontsize=10)
        tables = extract_borderless_tables(page, extract_text_blocks(page.get_text("dict"), 1))
        assert len(tables) == 2
        assert tables[0].rows[-1] == ["Extended variant", "12.3", "45.6"]
        assert tables[1].rows[-1] == ["Extended variant", "67.8", "90.1"]


def test_whole_page_assignment_reserves_a_grid_for_the_caption_without_an_alternative():
    first = RawTextBlock(1, [60, 150, 300, 182], "Table 1: A multiline caption", 10, 3, 30)
    second = RawTextBlock(1, [60, 280, 300, 295], "Table 2: Another caption", 10, 1, 25)
    upper = TableSnapshot([60, 80, 300, 130], [["First", "1"]] * 2, [], 2)
    lower = TableSnapshot([60, 200, 300, 260], [["Second", "2"]] * 2, [], 2)
    result = _assign_caption_grids([(first, [upper, lower]), (second, [lower])])
    assert result == [upper, lower]
    assert upper.caption_bbox == first.bbox and lower.caption_bbox == second.bbox


def test_augmenting_assignment_does_not_publish_one_grid_with_two_captions():
    captions = [RawTextBlock(1, [60, y, 300, y + 10], f"Table {i}: Results", 10, 1, 20)
                for i, y in enumerate([90, 150, 210], 1)]
    a = TableSnapshot([60, 30, 300, 70], [["A", "1"]] * 2, [], 2)
    b = TableSnapshot([60, 110, 300, 130], [["B", "2"]] * 2, [], 2)
    result = _assign_caption_grids([(captions[0], [a]), (captions[1], [a, b]),
                                   (captions[2], [b])])
    assert len(result) == 2
    assert len({tuple(table.bbox) for table in result}) == 2
