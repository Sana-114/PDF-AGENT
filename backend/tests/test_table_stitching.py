import fitz

from app.parsers.base import Page, TableCell, TableNode, TableSegment
from app.parsers.pymupdf_parser import PyMuPDFParser
from app.parsers.table_stitching import stitch_cross_page_tables


def _table(
    *,
    table_id: str,
    page_number: int,
    bbox: list[float],
    rows: list[list[str]],
    caption: str | None,
) -> TableNode:
    cells = [
        TableCell(row=row, column=column, text=value, page_number=page_number)
        for row, values in enumerate(rows)
        for column, value in enumerate(values)
    ]
    return TableNode(
        table_id=table_id,
        page_number=page_number,
        bbox=bbox,
        rows=rows,
        cells=cells,
        markdown="",
        caption=caption,
        segments=[TableSegment(page_number, bbox, 0, len(rows))],
    )


def _draw_grid_table(page, *, y_values: list[float], rows: list[list[str]]) -> None:
    x_values = [60, 220, 380, 535]
    for x_value in x_values:
        page.draw_line((x_value, y_values[0]), (x_value, y_values[-1]))
    for y_value in y_values:
        page.draw_line((x_values[0], y_value), (x_values[-1], y_value))
    for row_index, values in enumerate(rows):
        baseline = y_values[row_index] + 19
        for column_index, value in enumerate(values):
            page.insert_text((x_values[column_index] + 8, baseline), value, fontsize=9)


def _save_cross_page_table_pdf(path) -> None:
    document = fitz.open()
    first = document.new_page(width=595, height=842)
    first.insert_text((60, 45), "Cross-page Table Benchmark", fontsize=18)
    first.insert_text((60, 610), "Table 7. Ablation results", fontsize=10)
    _draw_grid_table(
        first,
        y_values=[630, 690, 750, 815],
        rows=[
            ["Model", "Score", "Cost"],
            ["Baseline", "91.2", "10"],
            ["Small", "92.0", "11"],
        ],
    )

    second = document.new_page(width=595, height=842)
    _draw_grid_table(
        second,
        y_values=[50, 95, 140, 185],
        rows=[
            ["Model", "Score", "Cost"],
            ["Medium", "93.1", "12"],
            ["Large", "94.0", "15"],
        ],
    )
    document.save(path)
    document.close()


def test_stitcher_merges_repeated_header_and_preserves_page_anchors() -> None:
    first = _table(
        table_id="p1-table-1",
        page_number=1,
        bbox=[60, 620, 535, 820],
        rows=[["Model", "Score"], ["A", "1"]],
        caption="Table 2. Results",
    )
    second = _table(
        table_id="p2-table-1",
        page_number=2,
        bbox=[60, 40, 535, 210],
        rows=[["Model", "Score"], ["B", "2"]],
        caption=None,
    )

    stitched = stitch_cross_page_tables(
        [first, second],
        [Page(1, 595, 842), Page(2, 595, 842)],
    )

    assert len(stitched) == 1
    assert stitched[0].rows == [["Model", "Score"], ["A", "1"], ["B", "2"]]
    assert [segment.page_number for segment in stitched[0].segments] == [1, 2]
    assert [(segment.row_start, segment.row_end) for segment in stitched[0].segments] == [
        (0, 2),
        (2, 3),
    ]
    assert {cell.page_number for cell in stitched[0].cells if cell.row == 2} == {2}


def test_stitcher_rejects_different_numbered_tables() -> None:
    first = _table(
        table_id="p1-table-1",
        page_number=1,
        bbox=[60, 620, 535, 820],
        rows=[["Model", "Score"], ["A", "1"]],
        caption="Table 2. Results",
    )
    second = _table(
        table_id="p2-table-1",
        page_number=2,
        bbox=[60, 40, 535, 210],
        rows=[["Model", "Score"], ["B", "2"]],
        caption="Table 3. Other results",
    )

    stitched = stitch_cross_page_tables(
        [first, second],
        [Page(1, 595, 842), Page(2, 595, 842)],
    )

    assert len(stitched) == 2


def test_parser_stitches_real_two_page_grid_table(tmp_path) -> None:
    path = tmp_path / "cross-page-table.pdf"
    _save_cross_page_table_pdf(path)

    parsed = PyMuPDFParser().parse(str(path))

    assert len(parsed.tables) == 1
    table = parsed.tables[0]
    assert table.caption == "Table 7. Ablation results"
    assert table.rows == [
        ["Model", "Score", "Cost"],
        ["Baseline", "91.2", "10"],
        ["Small", "92.0", "11"],
        ["Medium", "93.1", "12"],
        ["Large", "94.0", "15"],
    ]
    assert [segment.page_number for segment in table.segments] == [1, 2]
    assert table.segments[1].caption_block_id is None
    assert {cell.page_number for cell in table.cells} == {1, 2}
    assert table.markdown.count("| Large | 94.0 | 15 |") == 1
