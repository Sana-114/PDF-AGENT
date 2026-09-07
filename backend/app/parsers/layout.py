import re
from dataclasses import dataclass
from typing import Any

from app.parsers.base import (
    Block,
    FigureNode,
    FormulaNode,
    OutlineNode,
    ReferenceNode,
    TableCell,
    TableNode,
)

NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+){0,2}\.?|[A-Z]\.)\s+\S+")
KNOWN_HEADING = re.compile(
    r"^(?:abstract|摘要|keywords?|关键词|references|bibliography|参考文献|"
    r"acknowledg(?:e)?ments?|致谢|appendix(?:\s+[A-Z0-9]+)?|附录(?:\s*[A-Z0-9一二三四五六七八九十]+)?)$",
    re.IGNORECASE,
)
ABSTRACT_PREFIX = re.compile(r"^(?:abstract|摘要)\s*[:—-]?\s*", re.IGNORECASE)
REFERENCE_HEADING = re.compile(r"^(?:references|bibliography|参考文献)$", re.IGNORECASE)
APPENDIX_HEADING = re.compile(r"^(?:appendix\b|附录)", re.IGNORECASE)
REFERENCE_ENTRY = re.compile(r"^\s*(?:\[(\d+)\]\s*|(\d+)[.)]\s+)(.+)", re.DOTALL)
FIGURE_CAPTION = re.compile(r"^(?:fig(?:ure)?\.?\s*\d+|图\s*\d+)", re.IGNORECASE)
TABLE_CAPTION = re.compile(r"^(?:table\s*\d+|表\s*\d+)", re.IGNORECASE)
AFFILIATION_HINT = re.compile(
    r"(?:university|institute|laborator(?:y|ies)|department|school|college|"
    r"research center|大学|学院|研究院|实验室|研究中心|系)",
    re.IGNORECASE,
)
MATH_HINT = re.compile(r"[=∑∫√≈≤≥±×÷∞∂∇α-ωΑ-Ω]|\b(?:argmax|argmin|softmax)\b")


@dataclass(slots=True)
class RawTextBlock:
    page_number: int
    bbox: list[float]
    text: str
    font_size: float
    line_count: int
    char_weight: int


@dataclass(slots=True)
class TableSnapshot:
    bbox: list[float]
    rows: list[list[str]]
    cells: list[list[float] | None]
    column_count: int


def extract_text_blocks(page_dict: dict[str, Any], page_number: int) -> list[RawTextBlock]:
    blocks: list[RawTextBlock] = []
    for raw_block in page_dict.get("blocks", []):
        if int(raw_block.get("type", 0)) != 0:
            continue
        lines = raw_block.get("lines", [])
        spans = [span for line in lines for span in line.get("spans", [])]
        line_texts = [
            " ".join(str(span.get("text", "")).strip() for span in line.get("spans", []))
            for line in lines
        ]
        text = " ".join(" ".join(line_texts).split())
        if not text:
            continue
        sizes = [float(span.get("size", 0.0)) for span in spans if span.get("text", "").strip()]
        bbox = raw_block.get("bbox", (0, 0, 0, 0))
        blocks.append(
            RawTextBlock(
                page_number=page_number,
                bbox=_round_bbox(bbox),
                text=text,
                font_size=max(sizes, default=0.0),
                line_count=max(1, len(lines)),
                char_weight=max(1, len(text)),
            )
        )
    return blocks


def body_font_size(blocks: list[RawTextBlock]) -> float:
    weighted = sorted(
        (block.font_size, min(block.char_weight, 500))
        for block in blocks
        if 4 <= block.font_size <= 30
    )
    total = sum(weight for _, weight in weighted)
    if not total:
        return 10.0
    midpoint = total / 2
    running = 0
    for size, weight in weighted:
        running += weight
        if running >= midpoint:
            return size
    return weighted[-1][0]


def order_page_blocks(
    blocks: list[RawTextBlock], page_width: float
) -> list[tuple[RawTextBlock, int]]:
    """Order full-width bands, then each column inside the band below it."""

    wide: list[RawTextBlock] = []
    narrow: list[RawTextBlock] = []
    for block in blocks:
        width = block.bbox[2] - block.bbox[0]
        (wide if width >= page_width * 0.68 else narrow).append(block)

    wide.sort(key=lambda block: (block.bbox[1], block.bbox[0]))
    pending = list(narrow)
    ordered: list[tuple[RawTextBlock, int]] = []
    for band in wide:
        group = [block for block in pending if block.bbox[1] < band.bbox[1]]
        for block in sorted(group, key=lambda item: _column_key(item, page_width)):
            ordered.append((block, _column_number(block, page_width)))
            pending.remove(block)
        ordered.append((band, 0))
    for block in sorted(pending, key=lambda item: _column_key(item, page_width)):
        ordered.append((block, _column_number(block, page_width)))
    return ordered


def classify_block(
    raw: RawTextBlock,
    *,
    body_size: float,
    title: str | None,
) -> tuple[str, int | None]:
    text = raw.text.strip()
    if raw.page_number == 1 and title and _normalise(text) == _normalise(title):
        return "title", None
    if FIGURE_CAPTION.match(text):
        return "figure_caption", None
    if TABLE_CAPTION.match(text):
        return "table_caption", None
    if (
        len(text) <= 140
        and raw.line_count <= 2
        and NUMBERED_HEADING.match(text)
        and not MATH_HINT.search(text)
    ):
        prefix = text.split(maxsplit=1)[0].rstrip(".")
        return "heading", min(prefix.count(".") + 1, 3)
    if KNOWN_HEADING.match(text):
        if APPENDIX_HEADING.match(text):
            return "heading", 1
        return "heading", 1
    font_heading = (
        len(text) <= 140
        and raw.line_count <= 2
        and raw.font_size >= max(body_size * 1.16, body_size + 1.4)
        and not MATH_HINT.search(text)
        and not text.endswith((".", "。", ",", "，", ";", "；"))
    )
    if font_heading:
        return "heading", 1
    return "text", None


def build_outline(pages: list[Any]) -> list[OutlineNode]:
    roots: list[OutlineNode] = []
    stack: list[OutlineNode] = []
    for page in pages:
        for block in page.blocks:
            if block.type != "heading":
                continue
            level = block.level or 1
            node = OutlineNode(
                text=block.text,
                level=level,
                page_number=page.page_number,
                block_id=block.block_id,
                bbox=block.bbox,
            )
            while stack and stack[-1].level >= level:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            else:
                roots.append(node)
            stack.append(node)
    return roots


def extract_abstract(pages: list[Any]) -> str | None:
    collecting = False
    parts: list[str] = []
    for page in pages[:3]:
        for block in page.blocks:
            match = ABSTRACT_PREFIX.match(block.text)
            if match:
                collecting = True
                remainder = block.text[match.end() :].strip()
                if remainder:
                    parts.append(remainder)
                continue
            if collecting and block.type == "heading":
                return " ".join(parts).strip() or None
            if collecting and block.type in {"text", "formula"}:
                parts.append(block.text)
    return " ".join(parts).strip() or None


def extract_first_page_people(blocks: list[Block]) -> tuple[list[str], list[str]]:
    title_index = next((i for i, block in enumerate(blocks) if block.type == "title"), -1)
    candidates: list[str] = []
    for block in blocks[title_index + 1 :]:
        if ABSTRACT_PREFIX.match(block.text) or block.type == "heading":
            break
        if block.type in {"text", "figure_caption", "table_caption"}:
            candidates.append(block.text)
    if title_index < 0 or not candidates:
        return [], []

    affiliations = [text for text in candidates if AFFILIATION_HINT.search(text) or "@" in text]
    author_lines = [text for text in candidates if text not in affiliations]
    authors: list[str] = []
    for line in author_lines[:3]:
        cleaned = re.sub(r"[\d*†‡]+", "", line).strip()
        if len(cleaned) > 240 or len(cleaned.split()) > 30:
            continue
        parts = re.split(r"\s*(?:,|;|\band\b|、|，)\s*", cleaned, flags=re.IGNORECASE)
        authors.extend(part for part in parts if 1 < len(part) <= 80)
    return _dedupe(authors), _dedupe(affiliations)


def extract_references(pages: list[Any]) -> list[ReferenceNode]:
    references: list[ReferenceNode] = []
    active = False
    for page in pages:
        for block in page.blocks:
            if block.type == "heading" and is_reference_heading(block.text):
                active = True
                continue
            if active and block.type == "heading" and is_appendix_heading(block.text):
                active = False
                continue
            if not active or block.type in {"figure_caption", "table_caption"}:
                continue
            match = REFERENCE_ENTRY.match(block.text)
            if match:
                label = match.group(1) or match.group(2) or str(len(references) + 1)
                references.append(
                    ReferenceNode(
                        reference_id=f"ref-{label}",
                        label=label,
                        text=match.group(3).strip(),
                        page_number=page.page_number,
                        bbox=block.bbox,
                        block_ids=[block.block_id],
                    )
                )
            elif references and block.type != "heading":
                current = references[-1]
                current.text = f"{current.text} {block.text}".strip()
                current.bbox = union_bbox([current.bbox, block.bbox])
                current.block_ids.append(block.block_id)
    return references


def extract_formulas(pages: list[Any]) -> list[FormulaNode]:
    formulas: list[FormulaNode] = []
    for page in pages:
        for block in page.blocks:
            if block.type != "text" or len(block.text) > 240 or not MATH_HINT.search(block.text):
                continue
            block.type = "formula"
            formulas.append(
                FormulaNode(
                    formula_id=f"formula-{len(formulas) + 1}",
                    page_number=page.page_number,
                    bbox=block.bbox,
                    text=block.text,
                    block_id=block.block_id,
                )
            )
    return formulas


def make_table_node(
    table: TableSnapshot,
    *,
    page_number: int,
    table_index: int,
    caption: Block | None,
) -> TableNode:
    rows = table.rows
    column_count = table.column_count
    raw_cells = table.cells
    cells: list[TableCell] = []
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            flat_index = row_index * column_count + column_index if column_count else -1
            cell_bbox = raw_cells[flat_index] if 0 <= flat_index < len(raw_cells) else None
            cells.append(
                TableCell(
                    row=row_index,
                    column=column_index,
                    text=value,
                    bbox=_round_bbox(cell_bbox) if cell_bbox else None,
                )
            )
    return TableNode(
        table_id=f"p{page_number}-table-{table_index}",
        page_number=page_number,
        bbox=_round_bbox(table.bbox),
        rows=rows,
        cells=cells,
        markdown=rows_to_markdown(rows),
        caption=caption.text if caption else None,
        caption_block_id=caption.block_id if caption else None,
    )


def snapshot_table(table: Any) -> TableSnapshot:
    raw_rows = table.extract() or []
    rows = [["" if value is None else str(value).strip() for value in row] for row in raw_rows]
    cells = [
        _round_bbox(cell) if cell is not None else None
        for cell in list(getattr(table, "cells", []))
    ]
    return TableSnapshot(
        bbox=_round_bbox(table.bbox),
        rows=rows,
        cells=cells,
        column_count=int(
            getattr(table, "col_count", max((len(row) for row in rows), default=0))
        ),
    )


def rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalised = [row + [""] * (width - len(row)) for row in rows]
    escaped = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row] for row in normalised]
    lines = ["| " + " | ".join(row) + " |" for row in escaped]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def nearest_caption(
    blocks: list[Block], bbox: list[float], caption_type: str, max_distance: float = 100
) -> Block | None:
    candidates = [block for block in blocks if block.type == caption_type]
    if not candidates:
        return None

    def distance(block: Block) -> float:
        if block.bbox[1] >= bbox[3]:
            return block.bbox[1] - bbox[3]
        if bbox[1] >= block.bbox[3]:
            return bbox[1] - block.bbox[3]
        return 0.0

    candidate = min(candidates, key=distance)
    return candidate if distance(candidate) <= max_distance else None


def make_figure_nodes(
    image_infos: list[dict[str, Any]],
    *,
    page_number: int,
    page_area: float,
    blocks: list[Block],
) -> list[FigureNode]:
    figures: list[FigureNode] = []
    for image_index, info in enumerate(image_infos, start=1):
        bbox = _round_bbox(info.get("bbox", (0, 0, 0, 0)))
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        if not area or (page_area and area / page_area >= 0.75):
            continue
        caption = nearest_caption(blocks, bbox, "figure_caption")
        xref = info.get("xref")
        figures.append(
            FigureNode(
                figure_id=f"p{page_number}-figure-{len(figures) + 1}",
                page_number=page_number,
                bbox=bbox,
                image_index=image_index,
                xref=int(xref) if isinstance(xref, int) and xref > 0 else None,
                caption=caption.text if caption else None,
                caption_block_id=caption.block_id if caption else None,
            )
        )
    return figures


def appendix_nodes(outline: list[OutlineNode]) -> list[OutlineNode]:
    return [node for node in outline if is_appendix_heading(node.text)]


def is_reference_heading(text: str) -> bool:
    without_number = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", text)
    return bool(REFERENCE_HEADING.match(without_number.strip()))


def is_appendix_heading(text: str) -> bool:
    without_number = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", text)
    return bool(APPENDIX_HEADING.match(without_number.strip()))


def union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(box[0] for box in boxes), 2),
        round(min(box[1] for box in boxes), 2),
        round(max(box[2] for box in boxes), 2),
        round(max(box[3] for box in boxes), 2),
    ]


def _column_number(block: RawTextBlock, page_width: float) -> int:
    return 1 if (block.bbox[0] + block.bbox[2]) / 2 < page_width / 2 else 2


def _column_key(block: RawTextBlock, page_width: float) -> tuple[int, float, float]:
    return (_column_number(block, page_width), block.bbox[1], block.bbox[0])


def _round_bbox(values: Any) -> list[float]:
    return [round(float(value), 2) for value in values]


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
