"""Lightweight caption-guided layout recovery for raster-only PDF pages."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from typing import Any

import fitz

from app.parsers.layout import (
    RawTextBlock,
    TableSnapshot,
    is_figure_caption,
    is_table_caption,
)

RENDER_DPI = 96
DARK_PIXEL = 170
INK_PIXEL = 225


@dataclass(slots=True)
class RasterLine:
    position: int
    start: int
    end: int


def detect_raster_layout(
    page: fitz.Page,
    *,
    textpage: fitz.TextPage,
    raw_blocks: list[RawTextBlock],
    band_reader: Callable | None = None,
) -> tuple[list[TableSnapshot], list[dict[str, Any]]]:
    """Recover ruled tables and caption-adjacent figures from one OCR page.

    Detection is deliberately caption-gated. It provides auditable bounding boxes
    without treating the full-page scan image as a figure or guessing structures on
    ordinary prose pages.
    """

    table_captions = [block for block in raw_blocks if is_table_caption(block.text)]
    figure_captions = [block for block in raw_blocks if is_figure_caption(block.text)]
    if not table_captions and not figure_captions:
        return [], []

    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72),
        colorspace=fitz.csGRAY,
        alpha=False,
        annots=False,
    )
    words = [
        {
            "bbox": [float(value) for value in word[:4]],
            "text": str(word[4]).strip(),
        }
        for word in page.get_text("words", textpage=textpage, sort=True)
        if str(word[4]).strip()
    ]
    tables = _detect_tables(page, pixmap, table_captions, words, band_reader)
    figures = _detect_figures(page, pixmap, figure_captions)
    return tables, figures


def _detect_tables(
    page: fitz.Page,
    pixmap: fitz.Pixmap,
    captions: list[RawTextBlock],
    words: list[dict[str, Any]],
    band_reader: Callable | None = None,
) -> list[TableSnapshot]:
    horizontal = _horizontal_lines(pixmap)
    vertical = _vertical_lines(pixmap)
    components = _grid_components(horizontal, vertical)
    if not components:
        return []

    scale_x = page.rect.width / pixmap.width
    scale_y = page.rect.height / pixmap.height
    tables: list[TableSnapshot] = []
    used_components: set[int] = set()
    for caption in captions:
        column = _column_bounds(caption.bbox, page.rect.width)
        candidates: list[tuple[float, int, list[RasterLine], list[RasterLine]]] = []
        for index, (rows, columns) in enumerate(components):
            if index in used_components:
                continue
            bbox = _component_bbox(rows, columns, scale_x, scale_y)
            merged_caption = (caption.line_count >= 4 and caption.bbox[1] < bbox[1]
                              and bbox[1] < caption.bbox[3] <= bbox[3] + 3)
            if bbox[1] + 3 < caption.bbox[3] and not merged_caption:
                continue
            if bbox[1] - caption.bbox[3] > page.rect.height * 0.42:
                continue
            if _horizontal_overlap(bbox, column) < 0.45:
                continue
            candidates.append((max(0.0, bbox[1] - caption.bbox[3]), index, rows, columns))
        if not candidates:
            continue
        _, component_index, rows, columns = min(candidates, key=lambda item: item[0])
        used_components.add(component_index)
        # Repeated horizontal rules extending past the outermost verticals
        # identify open-sided tables. A single stray line is insufficient.
        left = round(median(r.start for r in rows))
        right = round(median(r.end for r in rows))
        extended = (len(rows) >= 3 and columns[0].position - left >= 12
                    and right - columns[-1].position >= 12
                    and sum(abs(r.start-left) <= 3 and abs(r.end-right) <= 3
                            for r in rows) >= len(rows) * 2 / 3)
        internal = [v.position * scale_x for v in columns]
        if extended:
            columns = [RasterLine(left, 0, 0), *columns, RasterLine(right, 0, 0)]
        grid = _table_snapshot(rows, columns, words, scale_x, scale_y)
        if extended and band_reader and len(rows) <= 24:
            grid = _dense_band_grid(grid, internal, band_reader) or grid
        tables.append(grid)
    return tables


def _dense_band_grid(grid, rules, read_band):
    """Use a full numeric row as anchors only when another band confirms their positions."""
    bands = [fitz.Rect(grid.bbox[0], row[1], grid.bbox[2], row[3])
             for row in grid.cells[::grid.column_count]]
    if len(bands) < 3 or any(r.height < 4 or r.height > 100 for r in bands):
        return None
    # Only refine a coarse grid that already contains a dense run of metrics.
    if not any(sum(t[0].isdigit() for t in cell.split()) >= 5 for row in grid.rows for cell in row):
        return None
    numeric = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[%kKMG])?")
    content = [read_band(r, rules) for r in bands]
    if any(not words and any(v.strip() for v in old)
           for old, words in zip(grid.rows, content, strict=True)):
        return None
    candidates = []
    for band in content[1:]:
        words = sorted(band, key=lambda w: w[0])
        numeric_count = sum(bool(numeric.fullmatch(w[4])) for w in words)
        if 6 <= len(words) <= 24 and numeric_count >= len(words) - 1:
            centers = [(w[0]+w[2])/2 for w in words]
            if (max(w[3] for w in words) - min(w[1] for w in words) < 18
                    and min(b - a for a, b in zip(centers, centers[1:], strict=False)) > 8):
                candidates.append(centers)
    if len(candidates) < 2:
        return None
    anchors = max(candidates, key=len)
    def column(word):
        return min(range(len(anchors)), key=lambda i: abs(anchors[i]-(word[0]+word[2])/2))
    if not any(other is not anchors and len(other) >= 6 and all(
        min(abs(x-a) for a in anchors) < 4 for x in other) for other in candidates):
        return None
    rows, cells = [], []
    for index, words in enumerate(content):
        lines = []
        for w in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
            cy = (w[1]+w[3])/2
            if not lines or (index and cy - lines[-1][0] > 4):
                lines.append((cy, []))
            lines[-1][1].append(w)
        for _, line in lines:
            buckets = [[] for _ in anchors]
            for w in sorted(line, key=lambda w: (round(w[1]/4), w[0])):
                buckets[column(w)].append(w)
            rows.append([" ".join(w[4] for w in bucket) for bucket in buckets])
            cells.extend([min(w[0] for w in b), min(w[1] for w in b),
                          max(w[2] for w in b), max(w[3] for w in b)] if b else None
                         for b in buckets)
    return TableSnapshot(grid.bbox, rows, cells, len(anchors))


def _horizontal_lines(pixmap: fitz.Pixmap) -> list[RasterLine]:
    samples = memoryview(pixmap.samples)
    minimum = max(36, int(pixmap.width * 0.12))
    candidates: list[RasterLine] = []
    for y in range(pixmap.height):
        row = samples[y * pixmap.stride : y * pixmap.stride + pixmap.width]
        run = _longest_dark_run(row, minimum)
        if run is not None:
            candidates.append(RasterLine(y, run[0], run[1]))
    return _merge_parallel_lines(candidates)


def _vertical_lines(pixmap: fitz.Pixmap) -> list[RasterLine]:
    samples = memoryview(pixmap.samples)
    minimum = max(28, int(pixmap.height * 0.045))
    candidates: list[RasterLine] = []
    for x in range(pixmap.width):
        values = samples[x : pixmap.stride * pixmap.height : pixmap.stride]
        run = _longest_dark_run(values, minimum)
        if run is not None:
            candidates.append(RasterLine(x, run[0], run[1]))
    return _merge_parallel_lines(candidates)


def _longest_dark_run(values: memoryview, minimum: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    start = -1
    last_dark = -1
    dark_count = 0
    gap = 0
    for index, value in enumerate(values):
        if value <= DARK_PIXEL:
            if start < 0:
                start = index
            last_dark = index
            dark_count += 1
            gap = 0
        elif start >= 0:
            gap += 1
            if gap <= 2:
                continue
            span = last_dark - start + 1
            if span >= minimum and dark_count / span >= 0.78:
                if best is None or span > best[1] - best[0] + 1:
                    best = (start, last_dark)
            start = -1
            last_dark = -1
            dark_count = 0
            gap = 0
    if start >= 0:
        span = last_dark - start + 1
        if span >= minimum and dark_count / span >= 0.78:
            if best is None or span > best[1] - best[0] + 1:
                best = (start, last_dark)
    return best


def _merge_parallel_lines(lines: list[RasterLine]) -> list[RasterLine]:
    if not lines:
        return []
    merged: list[list[RasterLine]] = []
    for line in sorted(lines, key=lambda item: item.position):
        if (
            merged
            and line.position - merged[-1][-1].position <= 3
            and _interval_overlap(line.start, line.end, merged[-1][-1].start, merged[-1][-1].end)
            >= 0.7
        ):
            merged[-1].append(line)
        else:
            merged.append([line])
    result = []
    for group in merged:
        representative = max(group, key=lambda item: item.end - item.start)
        result.append(
            RasterLine(
                position=round(sum(item.position for item in group) / len(group)),
                start=representative.start,
                end=representative.end,
            )
        )
    return result


def _grid_components(
    horizontal: list[RasterLine], vertical: list[RasterLine]
) -> list[tuple[list[RasterLine], list[RasterLine]]]:
    horizontal_edges: dict[int, list[int]] = {index: [] for index in range(len(horizontal))}
    vertical_edges: dict[int, list[int]] = {index: [] for index in range(len(vertical))}
    for row_index, row in enumerate(horizontal):
        for column_index, column in enumerate(vertical):
            if (
                row.start - 3 <= column.position <= row.end + 3
                and column.start - 3 <= row.position <= column.end + 3
            ):
                horizontal_edges[row_index].append(column_index)
                vertical_edges[column_index].append(row_index)

    components: list[tuple[list[RasterLine], list[RasterLine]]] = []
    visited_rows: set[int] = set()
    visited_columns: set[int] = set()
    for start_row, edges in horizontal_edges.items():
        if start_row in visited_rows or len(edges) < 2:
            continue
        queue: deque[tuple[str, int]] = deque([("row", start_row)])
        row_ids: set[int] = set()
        column_ids: set[int] = set()
        while queue:
            kind, index = queue.popleft()
            if kind == "row":
                if index in row_ids:
                    continue
                row_ids.add(index)
                queue.extend(("column", value) for value in horizontal_edges[index])
            else:
                if index in column_ids:
                    continue
                column_ids.add(index)
                queue.extend(("row", value) for value in vertical_edges[index])
        visited_rows.update(row_ids)
        visited_columns.update(column_ids)
        if len(row_ids) >= 3 and len(column_ids) >= 2:
            rows = sorted((horizontal[index] for index in row_ids), key=lambda item: item.position)
            columns = sorted(
                (vertical[index] for index in column_ids), key=lambda item: item.position
            )
            if len(rows) <= 40 and len(columns) <= 30:
                components.append((rows, columns))
    return components


def _table_snapshot(
    horizontal: list[RasterLine],
    vertical: list[RasterLine],
    words: list[dict[str, Any]],
    scale_x: float,
    scale_y: float,
) -> TableSnapshot:
    xs = [line.position * scale_x for line in vertical]
    ys = [line.position * scale_y for line in horizontal]
    rows: list[list[str]] = []
    cells: list[list[float]] = []
    for row_index in range(len(ys) - 1):
        values: list[str] = []
        for column_index in range(len(xs) - 1):
            bbox = [xs[column_index], ys[row_index], xs[column_index + 1], ys[row_index + 1]]
            cells.append([round(value, 2) for value in bbox])
            cell_words = [
                word
                for word in words
                if _bbox_center_inside(word["bbox"], bbox, padding=1.5)
            ]
            cell_words.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
            values.append(" ".join(str(item["text"]) for item in cell_words))
        rows.append(values)
    return TableSnapshot(
        bbox=[round(xs[0], 2), round(ys[0], 2), round(xs[-1], 2), round(ys[-1], 2)],
        rows=rows,
        cells=cells,
        column_count=max(0, len(xs) - 1),
    )


def _detect_figures(
    page: fitz.Page,
    pixmap: fitz.Pixmap,
    captions: list[RawTextBlock],
) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for caption in captions:
        column = _column_bounds(caption.bbox, page.rect.width)
        bbox = _ink_region_above(page, pixmap, caption.bbox, column)
        if bbox is None:
            continue
        if any(_bbox_iou(bbox, item["bbox"]) >= 0.7 for item in figures):
            continue
        figures.append({"bbox": bbox, "xref": None, "source": "raster_caption_region"})
    return figures


def _ink_region_above(
    page: fitz.Page,
    pixmap: fitz.Pixmap,
    caption_bbox: list[float],
    column: list[float],
) -> list[float] | None:
    scale_x = pixmap.width / page.rect.width
    scale_y = pixmap.height / page.rect.height
    x0 = max(0, round(column[0] * scale_x))
    x1 = min(pixmap.width - 1, round(column[1] * scale_x))
    caption_top = max(1, round(caption_bbox[1] * scale_y) - 2)
    maximum_height = max(24, round(page.rect.height * 0.42 * scale_y))
    minimum_ink = max(2, round((x1 - x0) * 0.003))
    samples = memoryview(pixmap.samples)

    active_rows: list[int] = []
    blank_run = 0
    started = False
    for y in range(caption_top - 1, max(-1, caption_top - maximum_height), -1):
        row = samples[y * pixmap.stride + x0 : y * pixmap.stride + x1 + 1]
        active = sum(value <= INK_PIXEL for value in row) >= minimum_ink
        if active:
            started = True
            blank_run = 0
            active_rows.append(y)
        elif started:
            blank_run += 1
            if blank_run >= 14:
                break
    if not active_rows:
        return None

    y0 = min(active_rows)
    y1 = max(active_rows)
    if y1 - y0 < max(20, pixmap.height * 0.035):
        return None
    ink_x: list[int] = []
    for y in range(y0, y1 + 1):
        row = samples[y * pixmap.stride + x0 : y * pixmap.stride + x1 + 1]
        ink_x.extend(x0 + index for index, value in enumerate(row) if value <= INK_PIXEL)
    if not ink_x:
        return None
    pdf_bbox = [
        min(ink_x) / scale_x,
        y0 / scale_y,
        (max(ink_x) + 1) / scale_x,
        (y1 + 1) / scale_y,
    ]
    area = max(0.0, pdf_bbox[2] - pdf_bbox[0]) * max(0.0, pdf_bbox[3] - pdf_bbox[1])
    if area < page.rect.width * page.rect.height * 0.012:
        return None
    return [round(value, 2) for value in pdf_bbox]


def _column_bounds(bbox: list[float], page_width: float) -> list[float]:
    width = bbox[2] - bbox[0]
    if width >= page_width * 0.62:
        return [page_width * 0.04, page_width * 0.96]
    midpoint = page_width / 2
    if (bbox[0] + bbox[2]) / 2 <= midpoint:
        return [page_width * 0.04, midpoint - page_width * 0.015]
    return [midpoint + page_width * 0.015, page_width * 0.96]


def _component_bbox(
    horizontal: list[RasterLine],
    vertical: list[RasterLine],
    scale_x: float,
    scale_y: float,
) -> list[float]:
    return [
        min(line.position for line in vertical) * scale_x,
        min(line.position for line in horizontal) * scale_y,
        max(line.position for line in vertical) * scale_x,
        max(line.position for line in horizontal) * scale_y,
    ]


def _horizontal_overlap(first: list[float], second: list[float]) -> float:
    overlap = max(0.0, min(first[2], second[1]) - max(first[0], second[0]))
    width = max(1.0, first[2] - first[0])
    return overlap / width


def _interval_overlap(start: int, end: int, other_start: int, other_end: int) -> float:
    overlap = max(0, min(end, other_end) - max(start, other_start) + 1)
    shortest = max(1, min(end - start + 1, other_end - other_start + 1))
    return overlap / shortest


def _bbox_center_inside(source: list[float], target: list[float], *, padding: float = 0) -> bool:
    center_x = (source[0] + source[2]) / 2
    center_y = (source[1] + source[3]) / 2
    return (
        target[0] - padding <= center_x <= target[2] + padding
        and target[1] - padding <= center_y <= target[3] + padding
    )


def _bbox_iou(first: list[float], second: list[float]) -> float:
    intersection_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    intersection_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = intersection_width * intersection_height
    if not intersection:
        return 0.0
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    return intersection / max(1.0, first_area + second_area - intersection)
