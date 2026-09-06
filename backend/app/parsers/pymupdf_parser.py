import re
from pathlib import Path

import fitz

from app.parsers.base import Block, Page, ParsedDocument

HEADING_PATTERN = re.compile(r"^(?:\d+(?:\.\d+){0,2}|[A-Z])\.?\s+\S+")


class PyMuPDFParser:
    """Fast baseline parser; Docling/OCR adapters can implement the same interface."""

    name = "pymupdf"
    version = fitz.VersionBind

    def parse(self, path: str) -> ParsedDocument:
        document = fitz.open(path)
        try:
            pages: list[Page] = []
            text_parts: list[str] = []
            for page_index, pdf_page in enumerate(document):
                page_blocks: list[Block] = []
                raw_blocks = pdf_page.get_text("blocks", sort=True)
                for order, raw in enumerate(raw_blocks):
                    x0, y0, x1, y1, text = raw[:5]
                    clean_text = " ".join(str(text).split())
                    if not clean_text:
                        continue
                    block_type, level = self._classify_block(clean_text)
                    page_blocks.append(
                        Block(
                            block_id=f"p{page_index + 1}-b{order + 1}",
                            type=block_type,
                            text=clean_text,
                            bbox=[round(float(v), 2) for v in (x0, y0, x1, y1)],
                            reading_order=order,
                            level=level,
                        )
                    )
                    text_parts.append(clean_text)

                pages.append(
                    Page(
                        page_number=page_index + 1,
                        width=round(float(pdf_page.rect.width), 2),
                        height=round(float(pdf_page.rect.height), 2),
                        blocks=page_blocks,
                    )
                )

            metadata_title = (document.metadata or {}).get("title")
            title = self._clean_metadata_title(metadata_title, path)
            if not title and pages:
                title = self._guess_title(document[0])

            warnings: list[str] = []
            if not text_parts:
                warnings.append("未检测到文本层，需要切换到 OCR 解析器。")

            return ParsedDocument(
                schema_version="0.1.0",
                title=title,
                authors=[],
                pages=pages,
                full_text="\n".join(text_parts),
                warnings=warnings,
            )
        finally:
            document.close()

    @staticmethod
    def _classify_block(text: str) -> tuple[str, int | None]:
        if len(text) <= 160 and HEADING_PATTERN.match(text):
            prefix = text.split(maxsplit=1)[0].rstrip(".")
            level = min(prefix.count(".") + 1, 3)
            return "heading", level
        return "text", None

    @staticmethod
    def _clean_metadata_title(title: str | None, path: str) -> str | None:
        if not title:
            return None
        cleaned = " ".join(title.split())
        if cleaned.casefold() in {"untitled", Path(path).stem.casefold()}:
            return None
        return cleaned

    @staticmethod
    def _guess_title(first_page: fitz.Page) -> str | None:
        candidates: list[tuple[float, str]] = []
        page_dict = first_page.get_text("dict")
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = " ".join(str(span.get("text", "")).strip() for span in spans).strip()
                if 8 <= len(text) <= 240 and spans:
                    largest_size = max(float(span.get("size", 0)) for span in spans)
                    candidates.append((largest_size, text))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], len(item[1])))[1]

