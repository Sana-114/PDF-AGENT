"""Caption-gated recovery for borderless native and OCR text tables."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from statistics import median

import fitz

from app.parsers.layout import (
    RawTextBlock,
    TableSnapshot,
    is_figure_caption,
    is_probable_section_heading,
    is_table_caption,
    union_bbox,
)

CELL_VALUE = re.compile(r"(?:[-+−]?\d[\d,.]*(?:[eE][-+]?\d+)?%?|[-–—]|N/A)", re.I)


@dataclass(slots=True)
class PositionedWord:
    bbox: list[float]
    text: str


@dataclass(slots=True)
class TextCell:
    bbox: list[float]
    text: str


def extract_borderless_tables(
    page: fitz.Page,
    raw_blocks: list[RawTextBlock],
    *,
    textpage: fitz.TextPage | None = None,
    excluded_boxes: list[list[float]] | None = None,
) -> list[TableSnapshot]:
    """Recover aligned text tables adjacent to numbered captions.

    The routine does not search arbitrary prose. A numbered table caption and at
    least two consistently aligned multi-cell rows are required.
    """

    captions = [block for block in raw_blocks if is_table_caption(block.text)]
    if not captions:
        return []
    words = [
        PositionedWord(
            bbox=[round(float(value), 2) for value in word[:4]],
            text=str(word[4]).strip(),
        )
        for word in page.get_text("words", textpage=textpage, sort=True)
        if str(word[4]).strip()
    ]
    options: list[tuple[RawTextBlock, list[TableSnapshot]]] = []
    for caption in sorted(captions, key=lambda item: item.bbox[1]):
        candidates: list[TableSnapshot] = []
        for above in (False, True):
            region = _candidate_region(
                caption, raw_blocks, page.rect.width, above=above,
                excluded_boxes=excluded_boxes,
                allow_merged_caption=textpage is not None,
            )
            if region is None:
                continue
            region_words = [
                word for word in words
                if _center_inside(word.bbox, region)
                and not any(_center_inside(word.bbox, box) for box in excluded_boxes or [])
            ]
            snapshot = _snapshot_from_words(region_words)
            if snapshot is not None:
                candidates.append(snapshot)
        if candidates:
            options.append((caption, candidates))
    return _assign_caption_grids(options)


def _assign_caption_grids(
    options: list[tuple[RawTextBlock, list[TableSnapshot]]],
) -> list[TableSnapshot]:
    """Assign one grid per caption, never reuse a neighboring grid twice.

    A multi-line caption may end closer to the NEXT grid than its own grid.
    Resolve the whole page's bounded candidate graph with augmenting paths,
    rather than independent nearest-neighbor choices or exponential search.
    """
    owners = {}
    assignments = {}

    def assign(index, visited):
        caption, candidates = options[index]
        for table in sorted(candidates, key=lambda t: max(
            0, t.bbox[1] - caption.bbox[3], caption.bbox[1] - t.bbox[3],
        )):
            key = tuple(table.bbox)
            if key in visited:
                continue
            visited.add(key)
            owner = owners.get(key)
            if owner is None or assign(owner, visited):
                owners[key] = index
                assignments[index] = table
                return True
        return False

    for index in sorted(range(len(options)), key=lambda i: len(options[i][1])):
        assign(index, set())
    results = []
    for index, table in sorted(assignments.items()):
        table.caption_bbox = list(options[index][0].bbox)
        results.append(table)
    return results


def _candidate_region(
    caption: RawTextBlock,
    raw_blocks: list[RawTextBlock],
    page_width: float,
    *,
    above: bool = False,
    excluded_boxes: list[list[float]] | None = None,
    allow_merged_caption: bool = False,
) -> list[float] | None:
    # OCR can put the caption and the entire table into ONE block. Use its
    # own bounded region only when it contains several data lines; the row
    # alignment gate still rejects ordinary prose and long captions.
    if (allow_merged_caption and not above and caption.line_count >= 4
            and caption.bbox[3] - caption.bbox[1] >= 50
            and sum(character.isdigit() for character in caption.text) >= 4):
        return list(caption.bbox)
    adjacent = sorted(
        (
            block
            for block in raw_blocks
            if block is not caption and (
                block.bbox[3] <= caption.bbox[1] + 1 if above
                else block.bbox[1] >= caption.bbox[3] - 1
            )
        ),
        key=lambda item: (-item.bbox[3] if above else item.bbox[1], item.bbox[0]),
    )
    selected: list[RawTextBlock] = []
    cursor = caption.bbox[1] if above else caption.bbox[3]
    for block in adjacent:
        if _horizontal_overlap(block.bbox, caption.bbox, page_width) < 0.15:
            continue
        if (
            is_table_caption(block.text) or is_figure_caption(block.text)
            or is_probable_section_heading(block.text)
            or any(_center_inside(block.bbox, box) for box in excluded_boxes or [])
        ):
            break
        gap = cursor - block.bbox[3] if above else block.bbox[1] - cursor
        if gap > (60 if not selected else 24):
            break
        selected.append(block)
        cursor = min(cursor, block.bbox[1]) if above else max(cursor, block.bbox[3])
    if not selected:
        return None
    content_bbox = union_bbox([block.bbox for block in selected])
    return [
        max(0.0, content_bbox[0] - 3),
        content_bbox[1] - 2 if above else caption.bbox[3],
        min(page_width, content_bbox[2] + 3),
        caption.bbox[1] if above else content_bbox[3] + 2,
    ]


def _snapshot_from_words(words: list[PositionedWord]) -> TableSnapshot | None:
    rows = _group_rows(words)
    split_rows = [_split_cells(row) for row in rows]
    first_multicell = next((index for index, row in enumerate(split_rows) if len(row) >= 2), None)
    if first_multicell is None:
        return None
    split_rows = split_rows[first_multicell:]
    eligible_counts = [len(row) for row in split_rows if 2 <= len(row) <= 16]
    if not eligible_counts:
        return None
    frequencies = Counter(eligible_counts)
    column_count = max(frequencies, key=lambda count: (frequencies[count], count))
    anchor_rows = [row for row in split_rows if len(row) == column_count]
    if len(anchor_rows) < 2:
        return None
    anchors = [
        median(row[column].bbox[0] for row in anchor_rows)
        for column in range(column_count)
    ]

    output_rows: list[list[str]] = []
    output_cells: list[list[float] | None] = []
    accepted_boxes: list[list[float]] = []
    for index, row in enumerate(split_rows):
        if not row:
            continue
        # A full-width prose line is not a sparse table row. Keep short cell
        # continuations, but stop before a single cell crosses the next column.
        if len(row) == 1:
            column = min(range(column_count), key=lambda i: abs(row[0].bbox[0] - anchors[i]))
            spans_columns = any(row[0].bbox[0] + 3 < anchor < row[0].bbox[2] - 3
                                for anchor in anchors[column + 1:])
            following = split_rows[index + 1] if index + 1 < len(split_rows) else []
            # Short, spanning group headers are legitimate when followed by
            # another grid row. A trailing paragraph must not gain this exception.
            group_header = len(row[0].text.split()) <= 8 and 2 <= len(following) <= column_count
            if spans_columns and not group_header:
                break
        values = [""] * column_count
        boxes: list[list[list[float]]] = [[] for _ in range(column_count)]
        nearest = [min(range(column_count), key=lambda i: abs(cell.bbox[0] - anchors[i]))
                   for cell in row]
        # A blank metric and a fragmented last cell can cancel out in the cell
        # count. Respect a number aligned to a different established column.
        shifted_value = any(
            expected != actual and CELL_VALUE.fullmatch(cell.text)
            and abs(cell.bbox[0] - anchors[actual]) <= 6
            and abs(cell.bbox[0] - anchors[expected]) > 12
            for expected, (cell, actual) in enumerate(zip(row, nearest, strict=True))
        ) if len(row) == column_count else False
        assignments = (list(range(column_count)) if len(row) == column_count and not shifted_value
                       else nearest)
        for cell, column in zip(row, assignments, strict=True):
            values[column] = " ".join(value for value in (values[column], cell.text) if value)
            boxes[column].append(cell.bbox)
            accepted_boxes.append(cell.bbox)
        output_rows.append(values)
        output_cells.extend(union_bbox(group) if group else None for group in boxes)

    nonempty = sum(bool(value) for row in output_rows for value in row)
    if len(output_rows) < 2 or nonempty < column_count * 2:
        return None
    return TableSnapshot(
        bbox=union_bbox(accepted_boxes),
        rows=output_rows,
        cells=output_cells,
        column_count=column_count,
    )


def _group_rows(words: list[PositionedWord]) -> list[list[PositionedWord]]:
    rows: list[list[PositionedWord]] = []
    for word in sorted(words, key=lambda item: (item.bbox[1], item.bbox[0])):
        best_index = None
        best_overlap = 0.0
        for index, row in enumerate(rows):
            row_bbox = union_bbox([item.bbox for item in row])
            overlap = _vertical_overlap(word.bbox, row_bbox)
            if overlap > best_overlap:
                best_index = index
                best_overlap = overlap
        if best_index is not None and best_overlap >= 0.32:
            rows[best_index].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda item: item.bbox[0])
    return sorted(rows, key=lambda row: min(item.bbox[1] for item in row))


def _split_cells(words: list[PositionedWord]) -> list[TextCell]:
    if not words:
        return []
    typical_height = median(max(1.0, word.bbox[3] - word.bbox[1]) for word in words)
    minimum_gap = max(7.0, typical_height * 0.78)
    # Dense numeric columns can have only a few points of whitespace. Restrict
    # this extra boundary rule to a trailing run of standalone values, not
    # numbers scattered through prose or superscripts within a formula.
    start = len(words)
    while start and CELL_VALUE.fullmatch(words[start - 1].text):
        start -= 1
    dense_run = len(words) - start >= 2
    groups: list[list[PositionedWord]] = [[words[0]]]
    for index, word in enumerate(words[1:], start=1):
        previous_right = max(item.bbox[2] for item in groups[-1])
        gap = word.bbox[0] - previous_right
        previous = words[index - 1]
        starts_value = index > start or (index == start and (
            "." in word.text or word.text in {"-", "–", "—", "N/A"}
        ))
        spaced_thousands = bool(
            re.fullmatch(r"[-+]?\d{1,3}", previous.text)
            and re.fullmatch(r"\d{3}", word.text)
        )
        dense_boundary = (
            dense_run and starts_value and not spaced_thousands
            and gap >= max(1.0, typical_height * 0.08)
            and _vertical_overlap(previous.bbox, word.bbox) >= 0.75
        )
        if gap >= minimum_gap or dense_boundary:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [
        TextCell(
            bbox=union_bbox([word.bbox for word in group]),
            text=" ".join(word.text for word in group),
        )
        for group in groups
    ]


def _center_inside(bbox: list[float], region: list[float]) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    return region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]


def _horizontal_overlap(first: list[float], second: list[float], page_width: float) -> float:
    overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    shortest = max(1.0, min(first[2] - first[0], second[2] - second[0], page_width))
    return overlap / shortest


def _vertical_overlap(first: list[float], second: list[float]) -> float:
    overlap = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    shortest = max(1.0, min(first[3] - first[1], second[3] - second[1]))
    return overlap / shortest
