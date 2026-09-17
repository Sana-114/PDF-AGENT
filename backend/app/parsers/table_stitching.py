"""Conservatively join table segments that continue onto adjacent PDF pages."""

from __future__ import annotations

import re

from app.parsers.base import Page, TableCell, TableNode, TableSegment
from app.parsers.layout import TableSnapshot, rows_to_markdown

TABLE_LABEL = re.compile(r"^(?:table|表)\s*([0-9]+|[ivxlcdm]+)", re.IGNORECASE)
CONTINUATION_HINT = re.compile(
    r"(?:continued|cont\.?|续(?:表)?|接上页)",
    re.IGNORECASE,
)


def stitch_cross_page_tables(
    tables: list[TableNode],
    pages: list[Page],
) -> list[TableNode]:
    """Merge high-confidence table continuations while preserving page anchors.

    Only the final table near the bottom of one page and the first table near the
    top of the next page can be joined. Their column count and normalized
    horizontal geometry must agree. An explicit matching table number or a
    repeated header row is also required.
    """

    if len(tables) < 2:
        return tables
    page_by_number = {page.page_number: page for page in pages}
    ordered = sorted(tables, key=lambda item: (item.page_number, item.bbox[1]))
    stitched: list[TableNode] = []
    for table in ordered:
        if stitched and _is_continuation(stitched[-1], table, page_by_number):
            stitched[-1] = _merge_tables(stitched[-1], table)
        else:
            stitched.append(table)
    return stitched


def is_unlabelled_snapshot_continuation(
    first: TableSnapshot,
    second: TableSnapshot,
    *,
    first_page_size: tuple[float, float],
    second_page_size: tuple[float, float],
) -> bool:
    """Check whether an unlabelled native table is a safe continuation candidate."""

    first_width, first_height = first_page_size
    second_width, second_height = second_page_size
    if first.bbox[3] < first_height * 0.72 or second.bbox[1] > second_height * 0.28:
        return False
    if first.column_count < 2 or first.column_count != second.column_count:
        return False
    if not first.rows or not second.rows or not _same_row(first.rows[0], second.rows[0]):
        return False
    return _horizontal_geometry_matches(
        first.bbox,
        first_width,
        second.bbox,
        second_width,
    )


def _is_continuation(
    first: TableNode,
    second: TableNode,
    page_by_number: dict[int, Page],
) -> bool:
    first_segment = _last_segment(first)
    second_segment = _first_segment(second)
    if second_segment.page_number != first_segment.page_number + 1:
        return False
    first_page = page_by_number.get(first_segment.page_number)
    second_page = page_by_number.get(second_segment.page_number)
    if first_page is None or second_page is None:
        return False
    if first_segment.bbox[3] < first_page.height * 0.72:
        return False
    if second_segment.bbox[1] > second_page.height * 0.28:
        return False

    first_width = _column_count(first.rows)
    second_width = _column_count(second.rows)
    if first_width < 2 or first_width != second_width:
        return False
    if not _horizontal_geometry_matches(
        first_segment.bbox,
        first_page.width,
        second_segment.bbox,
        second_page.width,
    ):
        return False

    first_label = _caption_label(first.caption)
    second_label = _caption_label(second.caption)
    repeated_header = _same_row(first.rows[0], second.rows[0])
    if first_label and second_label:
        return first_label == second_label and (
            repeated_header
            or bool(CONTINUATION_HINT.search(second.caption or ""))
        )
    if second_label:
        return repeated_header and bool(CONTINUATION_HINT.search(second.caption or ""))
    return bool(first_label and repeated_header)


def _merge_tables(first: TableNode, second: TableNode) -> TableNode:
    repeated_header = bool(
        first.rows and second.rows and _same_row(first.rows[0], second.rows[0])
    )
    dropped_rows = 1 if repeated_header else 0
    appended_rows = second.rows[dropped_rows:]
    row_offset = len(first.rows)
    merged_rows = [*first.rows, *appended_rows]

    appended_cells = [
        TableCell(
            row=row_offset + cell.row - dropped_rows,
            column=cell.column,
            text=cell.text,
            bbox=cell.bbox,
            page_number=cell.page_number or second.page_number,
        )
        for cell in second.cells
        if cell.row >= dropped_rows
    ]
    merged_segments = [*(_segments(first))]
    for segment in _segments(second):
        adjusted_start = max(0, segment.row_start - dropped_rows) + row_offset
        adjusted_end = max(0, segment.row_end - dropped_rows) + row_offset
        if adjusted_end <= adjusted_start:
            continue
        merged_segments.append(
            TableSegment(
                page_number=segment.page_number,
                bbox=segment.bbox,
                row_start=adjusted_start,
                row_end=adjusted_end,
                caption_block_id=segment.caption_block_id,
            )
        )

    return TableNode(
        table_id=first.table_id,
        page_number=first.page_number,
        bbox=first.bbox,
        rows=merged_rows,
        cells=[*first.cells, *appended_cells],
        markdown=rows_to_markdown(merged_rows),
        caption=first.caption or second.caption,
        caption_block_id=first.caption_block_id or second.caption_block_id,
        segments=merged_segments,
    )


def _segments(table: TableNode) -> list[TableSegment]:
    if table.segments:
        return table.segments
    return [
        TableSegment(
            page_number=table.page_number,
            bbox=table.bbox,
            row_start=0,
            row_end=len(table.rows),
            caption_block_id=table.caption_block_id,
        )
    ]


def _first_segment(table: TableNode) -> TableSegment:
    return _segments(table)[0]


def _last_segment(table: TableNode) -> TableSegment:
    return _segments(table)[-1]


def _caption_label(caption: str | None) -> str | None:
    if not caption:
        return None
    match = TABLE_LABEL.match(" ".join(caption.split()))
    return match.group(1).casefold() if match else None


def _column_count(rows: list[list[str]]) -> int:
    return max((len(row) for row in rows), default=0)


def _same_row(first: list[str], second: list[str]) -> bool:
    if len(first) != len(second) or not first:
        return False
    normalised_first = [_normalise_cell(value) for value in first]
    normalised_second = [_normalise_cell(value) for value in second]
    return any(normalised_first) and normalised_first == normalised_second


def _normalise_cell(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _horizontal_geometry_matches(
    first: list[float],
    first_page_width: float,
    second: list[float],
    second_page_width: float,
) -> bool:
    if first_page_width <= 0 or second_page_width <= 0:
        return False
    first_left = first[0] / first_page_width
    first_right = first[2] / first_page_width
    second_left = second[0] / second_page_width
    second_right = second[2] / second_page_width
    return abs(first_left - second_left) <= 0.08 and abs(first_right - second_right) <= 0.08
