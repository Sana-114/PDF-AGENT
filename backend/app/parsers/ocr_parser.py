from pathlib import Path

import fitz

from app.parsers.diagnostics import PdfDiagnostics
from app.parsers.layout import RawTextBlock, TableSnapshot
from app.parsers.pymupdf_parser import PyMuPDFParser
from app.parsers.raster_layout import detect_raster_layout


class OcrUnavailableError(RuntimeError):
    pass


class SelectiveOcrParser(PyMuPDFParser):
    """Use native extraction when possible and Tesseract only for low-text pages."""

    name = "pymupdf+tesseract"

    def __init__(
        self,
        diagnostics: PdfDiagnostics,
        *,
        languages: str,
        dpi: int,
        min_text_chars: int,
        tessdata: str | None = None,
    ) -> None:
        self.diagnostics = diagnostics
        self.languages = languages
        self.dpi = dpi
        self.min_text_chars = min_text_chars
        self.tessdata = tessdata or None
        self.ocr_pages: list[int] = []
        self.raster_table_pages: list[int] = []
        self.raster_figure_pages: list[int] = []

    def parse(self, path: str, **kwargs):
        self.ocr_pages = []
        self.raster_table_pages = []
        self.raster_figure_pages = []
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
        }

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


def validate_tessdata(tessdata: str | None, languages: str) -> list[str]:
    directory = Path(tessdata or fitz.get_tessdata())
    missing = [
        language
        for language in languages.split("+")
        if not (directory / f"{language}.traineddata").exists()
    ]
    return missing
