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

    def to_dict(self) -> dict:
        result = asdict(self)
        result.pop("full_text", None)
        return result


class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, path: str) -> ParsedDocument: ...

