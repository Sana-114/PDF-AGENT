from pathlib import Path

import fitz

from app.parsers.diagnostics import PdfDiagnostics
from app.parsers.pymupdf_parser import PyMuPDFParser


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

    def parse(self, path: str, **kwargs):
        self.ocr_pages = []
        parsed = super().parse(path, **kwargs)
        if self.ocr_pages:
            parsed.warnings.append(
                f"已使用 Tesseract OCR 识别 {len(self.ocr_pages)} 页："
                + ", ".join(str(value) for value in self.ocr_pages[:30])
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
        }

    def _checkpoint_metadata(self) -> dict:
        return {"ocr_pages": self.ocr_pages}

    def _restore_checkpoint_metadata(self, metadata: dict) -> None:
        values = metadata.get("ocr_pages", [])
        if isinstance(values, list):
            self.ocr_pages = [int(value) for value in values]


def validate_tessdata(tessdata: str | None, languages: str) -> list[str]:
    directory = Path(tessdata or fitz.get_tessdata())
    missing = [
        language
        for language in languages.split("+")
        if not (directory / f"{language}.traineddata").exists()
    ]
    return missing
