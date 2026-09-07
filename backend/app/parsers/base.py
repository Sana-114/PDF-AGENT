from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(slots=True)
class Block:
    block_id: str
    type: str
    text: str
    bbox: list[float]
    reading_order: int
    level: int | None = None
    font_size: float | None = None
    column: int | None = None


@dataclass(slots=True)
class OutlineNode:
    text: str
    level: int
    page_number: int
    block_id: str
    bbox: list[float]
    children: list["OutlineNode"] = field(default_factory=list)


@dataclass(slots=True)
class TableCell:
    row: int
    column: int
    text: str
    bbox: list[float] | None = None


@dataclass(slots=True)
class TableNode:
    table_id: str
    page_number: int
    bbox: list[float]
    rows: list[list[str]]
    cells: list[TableCell]
    markdown: str
    caption: str | None = None
    caption_block_id: str | None = None


@dataclass(slots=True)
class FigureNode:
    figure_id: str
    page_number: int
    bbox: list[float]
    image_index: int
    xref: int | None = None
    caption: str | None = None
    caption_block_id: str | None = None


@dataclass(slots=True)
class FormulaNode:
    formula_id: str
    page_number: int
    bbox: list[float]
    text: str
    block_id: str
    representation: str = "text_candidate"


@dataclass(slots=True)
class ReferenceNode:
    reference_id: str
    label: str
    text: str
    page_number: int
    bbox: list[float]
    block_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Page:
    page_number: int
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument:
    schema_version: str
    title: str | None
    authors: list[str]
    pages: list[Page]
    full_text: str
    warnings: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str | None = None
    outline: list[OutlineNode] = field(default_factory=list)
    tables: list[TableNode] = field(default_factory=list)
    figures: list[FigureNode] = field(default_factory=list)
    formulas: list[FormulaNode] = field(default_factory=list)
    references: list[ReferenceNode] = field(default_factory=list)
    appendices: list[OutlineNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = asdict(self)
        result.pop("full_text", None)
        return result


class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, path: str) -> ParsedDocument: ...
