from pathlib import Path
from typing import Any

import fitz

from app.parsers.base import Block, Page, ParsedDocument
from app.parsers.layout import (
    RawTextBlock,
    appendix_nodes,
    body_font_size,
    build_outline,
    classify_block,
    extract_abstract,
    extract_first_page_people,
    extract_formulas,
    extract_references,
    extract_text_blocks,
    make_figure_nodes,
    make_table_node,
    nearest_caption,
    order_page_blocks,
    snapshot_table,
)


class PyMuPDFParser:
    """Layout-aware local parser with page and bounding-box provenance."""

    name = "pymupdf-layout"
    version = fitz.VersionBind

    def parse(self, path: str) -> ParsedDocument:
        document = fitz.open(path)
        try:
            warnings: list[str] = []
            raw_pages: list[list[RawTextBlock]] = []
            page_sizes: list[tuple[float, float]] = []
            page_images: list[list[dict[str, Any]]] = []
            page_tables: list[list[Any]] = []
            first_page_title_guess: str | None = None

            for page_index, pdf_page in enumerate(document):
                page_number = page_index + 1
                textpage = self._get_textpage(pdf_page, page_number)
                page_dict = pdf_page.get_text("dict", sort=False, textpage=textpage)
                raw_blocks = extract_text_blocks(page_dict, page_number)
                raw_pages.append(raw_blocks)
                page_sizes.append((float(pdf_page.rect.width), float(pdf_page.rect.height)))
                page_images.append(self._extract_image_info(pdf_page, warnings))
                page_tables.append(
                    self._extract_native_tables(pdf_page, textpage=textpage, warnings=warnings)
                )
                if page_index == 0:
                    first_page_title_guess = self._guess_title_from_blocks(raw_blocks)

            metadata_title = (document.metadata or {}).get("title")
            title = self._clean_metadata_title(metadata_title, path) or first_page_title_guess
            body_size = body_font_size([block for page in raw_pages for block in page])
            pages = self._build_pages(raw_pages, page_sizes, body_size=body_size, title=title)

            tables = []
            figures = []
            for page_index, page in enumerate(pages):
                for table_index, table in enumerate(page_tables[page_index], start=1):
                    caption = nearest_caption(page.blocks, list(table.bbox), "table_caption")
                    tables.append(
                        make_table_node(
                            table,
                            page_number=page.page_number,
                            table_index=table_index,
                            caption=caption,
                        )
                    )
                figures.extend(
                    make_figure_nodes(
                        page_images[page_index],
                        page_number=page.page_number,
                        page_area=page.width * page.height,
                        blocks=page.blocks,
                    )
                )

            authors, affiliations = extract_first_page_people(pages[0].blocks if pages else [])
            outline = build_outline(pages)
            text_parts = [block.text for page in pages for block in page.blocks]
            if not text_parts:
                warnings.append("未检测到文本层，需要切换到 OCR 解析器。")

            return ParsedDocument(
                schema_version="0.2.0",
                title=title,
                authors=authors,
                affiliations=affiliations,
                abstract=extract_abstract(pages),
                outline=outline,
                pages=pages,
                tables=tables,
                figures=figures,
                formulas=extract_formulas(pages),
                references=extract_references(pages),
                appendices=appendix_nodes(outline),
                full_text="\n".join(text_parts),
                warnings=warnings,
            )
        finally:
            document.close()

    def _get_textpage(self, page: fitz.Page, page_number: int) -> fitz.TextPage | None:
        del page, page_number
        return None

    @staticmethod
    def _build_pages(
        raw_pages: list[list[RawTextBlock]],
        page_sizes: list[tuple[float, float]],
        *,
        body_size: float,
        title: str | None,
    ) -> list[Page]:
        pages: list[Page] = []
        for page_index, raw_blocks in enumerate(raw_pages):
            width, height = page_sizes[page_index]
            blocks = []
            for order, (raw, column) in enumerate(order_page_blocks(raw_blocks, width)):
                block_type, level = classify_block(raw, body_size=body_size, title=title)
                blocks.append(
                    Block(
                        block_id=f"p{page_index + 1}-b{order + 1}",
                        type=block_type,
                        text=raw.text,
                        bbox=raw.bbox,
                        reading_order=order,
                        level=level,
                        font_size=round(raw.font_size, 2),
                        column=column,
                    )
                )
            pages.append(
                Page(
                    page_number=page_index + 1,
                    width=round(width, 2),
                    height=round(height, 2),
                    blocks=blocks,
                )
            )
        return pages

    @staticmethod
    def _extract_native_tables(
        page: fitz.Page,
        *,
        textpage: fitz.TextPage | None,
        warnings: list[str],
    ) -> list[Any]:
        if textpage is not None:
            return []
        try:
            finder = page.find_tables()
            return [snapshot_table(table) for table in finder.tables]
        except Exception as exc:
            warnings.append(f"第 {page.number + 1} 页表格检测跳过：{type(exc).__name__}")
            return []

    @staticmethod
    def _extract_image_info(page: fitz.Page, warnings: list[str]) -> list[dict[str, Any]]:
        try:
            return list(page.get_image_info(xrefs=True))
        except Exception as exc:
            warnings.append(f"第 {page.number + 1} 页图像检测跳过：{type(exc).__name__}")
            return []

    @staticmethod
    def _clean_metadata_title(title: str | None, path: str) -> str | None:
        if not title:
            return None
        cleaned = " ".join(title.split())
        if cleaned.casefold() in {"untitled", Path(path).stem.casefold()}:
            return None
        return cleaned

    @staticmethod
    def _guess_title_from_blocks(blocks: list[RawTextBlock]) -> str | None:
        candidates = [block for block in blocks if 8 <= len(block.text) <= 240]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.font_size, len(item.text))).text

    @staticmethod
    def _classify_block(text: str) -> tuple[str, int | None]:
        """Backward-compatible helper retained for parser extension tests."""

        raw = RawTextBlock(1, [0, 0, 0, 0], text, 10.0, 1, len(text))
        return classify_block(raw, body_size=10.0, title=None)

    @staticmethod
    def _guess_title(
        first_page: fitz.Page, textpage: fitz.TextPage | None = None
    ) -> str | None:
        page_dict = first_page.get_text("dict", textpage=textpage)
        return PyMuPDFParser._guess_title_from_blocks(extract_text_blocks(page_dict, 1))
