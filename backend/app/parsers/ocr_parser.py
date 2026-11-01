import hashlib
import json
from pathlib import Path

import fitz

from app.parsers.diagnostics import PdfDiagnostics
from app.parsers.front_matter_ocr import (
    author_grid_region,
    compatible_people,
    project_crop_blocks,
)
from app.parsers.layout import RawTextBlock, TableSnapshot, extract_text_blocks
from app.parsers.pymupdf_parser import PyMuPDFParser
from app.parsers.raster_layout import detect_raster_layout
from app.parsers.text_table import extract_borderless_tables


class OcrUnavailableError(RuntimeError):
    pass


class SelectiveOcrParser(PyMuPDFParser):
    """Use native extraction when possible and Tesseract only for low-text pages."""

    name = "pymupdf+tesseract"
    version = f"{fitz.VersionBind}-ocr-layout100"

    def __init__(
        self,
        diagnostics: PdfDiagnostics,
        *,
        languages: str,
        dpi: int,
        min_text_chars: int,
        tessdata: str | None = None,
        front_matter_languages: str = "",
    ) -> None:
        self.diagnostics = diagnostics
        self.languages = languages
        self.dpi = dpi
        self.min_text_chars = min_text_chars
        self.tessdata = tessdata or None
        self.front_matter_languages = front_matter_languages
        self.front_matter_refinements: list[dict] = []
        signature = json.dumps([languages, dpi, min_text_chars, front_matter_languages])
        self.version += "-" + hashlib.sha256(signature.encode()).hexdigest()[:10]
        self.ocr_pages: list[int] = []
        self.raster_table_pages: list[int] = []
        self.raster_figure_pages: list[int] = []

    def parse(self, path: str, **kwargs):
        self.ocr_pages = []
        self.raster_table_pages = []
        self.raster_figure_pages = []
        self.front_matter_refinements = []
        parsed = super().parse(path, **kwargs)
        if self.ocr_pages:
            parsed.warnings.append(
                f"已使用 Tesseract OCR 识别 {len(self.ocr_pages)} 页："
                + ", ".join(str(value) for value in self.ocr_pages[:30])
            )
        if self.raster_table_pages or self.raster_figure_pages:
            parsed.warnings.append(
                "扫描版式恢复："
                f"表格候选 {len(self.raster_table_pages)} 页，"
                f"图区域候选 {len(self.raster_figure_pages)} 页。"
            )
        if self.raster_table_pages:
            parsed.warnings.append(
                "扫描表格为 OCR 候选，复杂单元格、小数点及公式尚未逐项校验；"
                "精确数值请核对原页，不以候选数量代表内容准确率。"
            )
        return parsed

    def _get_textpage(self, page: fitz.Page, page_number: int) -> fitz.TextPage | None:
        native_chars = len("".join(page.get_text("text").split()))
        if native_chars >= self.min_text_chars:
            return None
        try:
            textpage = page.get_textpage_ocr(
                language=self.languages,
                dpi=self.dpi,
                full=True,
                tessdata=self.tessdata,
            )
        except RuntimeError as exc:
            location = self.tessdata or "自动检测路径"
            raise OcrUnavailableError(
                "Tesseract OCR 不可用。请检查语言包 "
                f"{self.languages} 和 tessdata 路径 {location}。"
            ) from exc
        self.ocr_pages.append(page_number)
        return textpage

    def processing_metadata(self) -> dict:
        return {
            "engine": "tesseract",
            "languages": self.languages,
            "dpi": self.dpi,
            "ocr_pages": self.ocr_pages,
            "ocr_page_count": len(self.ocr_pages),
            "raster_table_pages": self.raster_table_pages,
            "raster_table_page_count": len(self.raster_table_pages),
            "raster_figure_pages": self.raster_figure_pages,
            "raster_figure_page_count": len(self.raster_figure_pages),
            "front_matter_refinements": self.front_matter_refinements,
        }

    def _refine_text_blocks(
        self, page: fitz.Page, *, textpage: fitz.TextPage | None,
        raw_blocks: list[RawTextBlock], warnings: list[str],
    ) -> list[RawTextBlock]:
        if textpage is None or page.number != 0 or not self.front_matter_languages:
            return raw_blocks
        title = self._guess_title_from_blocks(raw_blocks)
        if not title:
            return raw_blocks
        for index, block in enumerate(raw_blocks):
            region = author_grid_region(block, page.rect)
            if region is None:
                continue
            if validate_tessdata(self.tessdata, self.front_matter_languages):
                warnings.append("首页作者区补充 OCR 缺少语言包，已保留页级原始识别结果。")
                return raw_blocks
            try:
                # Fixed bounded resolution: enlarging raster text alone does not
                # guarantee improved recognition, and cannot add source detail.
                dpi = 300
                pixmap = page.get_pixmap(clip=region, dpi=dpi, colorspace=fitz.csRGB,
                                        alpha=False, annots=False)
                payload = pixmap.pdfocr_tobytes(language=self.front_matter_languages,
                                               tessdata=self.tessdata)
                with fitz.open("pdf", payload) as cropped:
                    crop_page = cropped[0]
                    refined = project_crop_blocks(
                        extract_text_blocks(crop_page.get_text("dict"), 1),
                        origin=(pixmap.x * 72 / dpi, pixmap.y * 72 / dpi),
                        scale=(pixmap.width * 72 / dpi / crop_page.rect.width,
                               pixmap.height * 72 / dpi / crop_page.rect.height),
                    )
            except RuntimeError:
                warnings.append("首页作者区补充 OCR 不可用，已保留页级原始识别结果。")
                return raw_blocks
            candidate = [*raw_blocks[:index], *refined, *raw_blocks[index + 1:]]
            if not compatible_people(raw_blocks, candidate, title):
                warnings.append("首页作者区补充 OCR 名单不兼容，未替换原始识别结果。")
                return raw_blocks
            self.front_matter_refinements.append({
                "page_number": 1, "bbox": list(region), "dpi": dpi,
                "languages": self.front_matter_languages,
                "original_text": block.text,
                "refined_text": "\n".join(item.text for item in refined),
                "source": "original_page_crop_ocr",
            })
            warnings.append("首页作者区已补充局部 OCR，原识别文本及原页坐标保存在解析记录。")
            return candidate
        return raw_blocks

    def _extract_raster_layout(
        self,
        page: fitz.Page,
        *,
        textpage: fitz.TextPage | None,
        raw_blocks: list[RawTextBlock],
        warnings: list[str],
    ) -> tuple[list[TableSnapshot], list[dict]]:
        if textpage is None:
            return [], []
        try:
            tables, figures = detect_raster_layout(
                page,
                textpage=textpage,
                raw_blocks=raw_blocks,
            )
            for candidate in extract_borderless_tables(page, raw_blocks, textpage=textpage):
                overlaps = [index for index, table in enumerate(tables)
                            if self._table_overlap(candidate, table) >= 0.72]
                if not overlaps:
                    tables.append(candidate)
                elif candidate.column_count > 1 and tables[overlaps[0]].column_count == 1:
                    # Broken ruling lines may form a false single-column grid.
                    # Prefer independently aligned OCR columns in the same region.
                    tables[overlaps[0]] = candidate
            self._fill_empty_table_cells(page, tables, warnings)
            if tables:
                self.raster_table_pages.append(page.number + 1)
            if figures:
                self.raster_figure_pages.append(page.number + 1)
            return tables, figures
        except Exception as exc:
            warnings.append(
                f"第 {page.number + 1} 页扫描版式识别跳过：{type(exc).__name__}"
            )
            return [], []

    def _fill_empty_table_cells(
        self,
        page: fitz.Page,
        tables: list[TableSnapshot],
        warnings: list[str],
    ) -> None:
        """OCR empty ruled cells independently after the page-level OCR pass."""

        failures = 0
        for table in tables:
            if not table.cells or len(table.cells) > 64:
                continue
            for index, cell_bbox in enumerate(table.cells):
                row_index, column_index = divmod(index, table.column_count)
                if table.rows[row_index][column_index].strip() or cell_bbox is None:
                    continue
                rect = fitz.Rect(cell_bbox) + (2, 2, -2, -2)
                if rect.is_empty or rect.width < 4 or rect.height < 4:
                    continue
                try:
                    pixmap = page.get_pixmap(
                        clip=rect,
                        dpi=max(220, self.dpi),
                        colorspace=fitz.csRGB,
                        alpha=False,
                        annots=False,
                    )
                    payload = pixmap.pdfocr_tobytes(
                        language=self.languages,
                        tessdata=self.tessdata,
                    )
                    with fitz.open("pdf", payload) as cell_document:
                        value = " ".join(cell_document[0].get_text("text").split())
                    table.rows[row_index][column_index] = value
                except RuntimeError:
                    failures += 1
        if failures:
            warnings.append(
                f"第 {page.number + 1} 页有 {failures} 个表格单元格 OCR 失败，已保留空值。"
            )

    def _checkpoint_metadata(self) -> dict:
        return {
            "ocr_pages": self.ocr_pages,
            "raster_table_pages": self.raster_table_pages,
            "raster_figure_pages": self.raster_figure_pages,
            "front_matter_refinements": self.front_matter_refinements,
        }

    def _restore_checkpoint_metadata(self, metadata: dict) -> None:
        values = metadata.get("ocr_pages", [])
        if isinstance(values, list):
            self.ocr_pages = [int(value) for value in values]
        table_pages = metadata.get("raster_table_pages", [])
        if isinstance(table_pages, list):
            self.raster_table_pages = [int(value) for value in table_pages]
        figure_pages = metadata.get("raster_figure_pages", [])
        if isinstance(figure_pages, list):
            self.raster_figure_pages = [int(value) for value in figure_pages]
        refinements = metadata.get("front_matter_refinements", [])
        if isinstance(refinements, list):
            self.front_matter_refinements = refinements


def validate_tessdata(tessdata: str | None, languages: str) -> list[str]:
    directory = Path(tessdata or fitz.get_tessdata())
    missing = [
        language
        for language in languages.split("+")
        if not (directory / f"{language}.traineddata").exists()
    ]
    return missing
