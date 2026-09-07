from app.core.config import settings
from app.parsers.base import DocumentParser
from app.parsers.diagnostics import PdfContentKind, PdfDiagnostics, inspect_pdf
from app.parsers.ocr_parser import SelectiveOcrParser
from app.parsers.pymupdf_parser import PyMuPDFParser


class RoutedParser:
    """Attach a bounded PDF diagnostic decision to the selected parser."""

    def __init__(self, parser: DocumentParser, diagnostics: PdfDiagnostics) -> None:
        self.parser = parser
        self.diagnostics = diagnostics
        self.name = parser.name
        self.version = parser.version

    def parse(self, path: str):
        parsed = self.parser.parse(path)
        if self.diagnostics.content_kind == PdfContentKind.SCANNED_IMAGE:
            parsed.warnings.append(
                "检测到扫描图像型 PDF；当前使用原生文本兜底，后续需要 OCR 识别。"
            )
        elif self.diagnostics.content_kind == PdfContentKind.HYBRID:
            pages = ", ".join(str(value) for value in self.diagnostics.low_text_pages[:20])
            parsed.warnings.append(f"检测到混合型 PDF，低文本页面需要 OCR：{pages}")
        elif self.diagnostics.content_kind == PdfContentKind.EMPTY:
            parsed.warnings.append("PDF 样本页中未检测到文本或嵌入图像。")
        return parsed


def get_parser(path: str | None = None) -> DocumentParser:
    parser = PyMuPDFParser()
    if path is None:
        return parser
    diagnostics = inspect_pdf(path)
    if settings.ocr_enabled and diagnostics.content_kind in {
        PdfContentKind.SCANNED_IMAGE,
        PdfContentKind.HYBRID,
    }:
        return SelectiveOcrParser(
            diagnostics,
            languages=settings.ocr_languages,
            dpi=settings.ocr_dpi,
            min_text_chars=settings.ocr_min_text_chars,
            tessdata=settings.ocr_tessdata or None,
        )
    return RoutedParser(parser, diagnostics)
