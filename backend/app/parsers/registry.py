from app.parsers.base import DocumentParser
from app.parsers.pymupdf_parser import PyMuPDFParser


def get_parser() -> DocumentParser:
    # The registry is intentionally small for the MVP. A future router will select
    # Docling, GROBID or OCR based on text density and document type.
    return PyMuPDFParser()

