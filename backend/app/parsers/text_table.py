"""Caption-gated recovery for borderless native-text tables."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median

import fitz

from app.parsers.layout import RawTextBlock, TableSnapshot, is_table_caption, union_bbox


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
) -> list[TableSnapshot]:
    """Recover aligned text tables below numbered captions.

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
        for word in page.get_text("words", sort=True)
        if str(word[4]).strip()
    ]
    results: list[TableSnapshot] = []
    for caption in sorted(captions, key=lambda item: item.bbox[1]):
        region = _candidate_region(caption, raw_blocks, page.rect.width)
        if region is None:
            continue
        region_words = [word for word in words if _center_inside(word.bbox, region)]
        snapshot = _snapshot_from_words(region_words)
        if snapshot is not None:
            results.append(snapshot)
    return results


def _candidate_region(
    caption: RawTextBlock,
    raw_blocks: list[RawTextBlock],
    page_width: float,
) -> list[float] | None:
    below = sorted(
        (
            block
            for block in raw_blocks
            if block is not caption and block.bbox[1] >= caption.bbox[3] - 1
        ),
        key=lambda item: (item.bbox[1], item.bbox[0]),
    )
    selected: list[RawTextBlock] = []
    cursor = caption.bbox[3]
    for block in below:
        if is_table_caption(block.text):
            break
        gap = block.bbox[1] - cursor
        if gap > (30 if not selected else 24):
            break
        if _horizontal_overlap(block.bbox, caption.bbox, page_width) < 0.15:
            continue
        selected.append(block)
        cursor = max(cursor, block.bbox[3])
    if not selected:
        return None
    content_bbox = union_bbox([block.bbox for block in selected])
    return [
        max(0.0, content_bbox[0] - 3),
        max(caption.bbox[3], content_bbox[1] - 2),
        min(page_width, content_bbox[2] + 3),
        content_bbox[3] + 2,
    ]


def _snapshot_from_words(words: list[PositionedWord]) -> TableSnapshot | None:
    rows = _group_rows(words)
    split_rows = [_split_cells(row) for row in rows]
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
    for row in split_rows:
        if not row:
            continue
        values = [""] * column_count
        boxes: list[list[list[float]]] = [[] for _ in range(column_count)]
        if len(row) == column_count:
            assignments = list(range(column_count))
        else:
            assignments = [
                min(range(column_count), key=lambda index: abs(cell.bbox[0] - anchors[index]))
                for cell in row
            ]
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
    groups: list[list[PositionedWord]] = [[words[0]]]
    for word in words[1:]:
        previous_right = max(item.bbox[2] for item in groups[-1])
        if word.bbox[0] - previous_right >= minimum_gap:
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
