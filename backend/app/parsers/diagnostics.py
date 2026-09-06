from dataclasses import asdict, dataclass
from enum import StrEnum

import fitz


class PdfContentKind(StrEnum):
    NATIVE_TEXT = "native_text"
    SCANNED_IMAGE = "scanned_image"
    HYBRID = "hybrid"
    EMPTY = "empty"


@dataclass(slots=True)
class PdfDiagnostics:
    content_kind: PdfContentKind
    page_count: int
    sampled_pages: list[int]
    text_pages: list[int]
    image_pages: list[int]
    low_text_pages: list[int]
    average_text_chars: float
    native_text_ratio: float

    def to_dict(self) -> dict:
        result = asdict(self)
        result["content_kind"] = self.content_kind.value
        return result


def inspect_pdf(path: str, max_sample_pages: int = 12) -> PdfDiagnostics:
    """Inspect a bounded page sample without rendering the whole document."""

    document = fitz.open(path)
    try:
        page_count = len(document)
        indices = _sample_indices(page_count, max_sample_pages)
        text_pages: list[int] = []
        image_pages: list[int] = []
        low_text_pages: list[int] = []
        character_counts: list[int] = []

        for page_index in indices:
            page = document[page_index]
            text_chars = len("".join(page.get_text("text").split()))
            page_number = page_index + 1
            character_counts.append(text_chars)
            if text_chars >= 32:
                text_pages.append(page_number)
            else:
                low_text_pages.append(page_number)
            if page.get_images(full=True):
                image_pages.append(page_number)

        sample_count = len(indices)
        native_ratio = len(text_pages) / sample_count if sample_count else 0.0
        image_ratio = len(image_pages) / sample_count if sample_count else 0.0
        if not sample_count or (not text_pages and not image_pages):
            content_kind = PdfContentKind.EMPTY
        elif native_ratio >= 0.8:
            content_kind = PdfContentKind.NATIVE_TEXT
        elif native_ratio <= 0.2 and image_ratio >= 0.5:
            content_kind = PdfContentKind.SCANNED_IMAGE
        else:
            content_kind = PdfContentKind.HYBRID

        return PdfDiagnostics(
            content_kind=content_kind,
            page_count=page_count,
            sampled_pages=[index + 1 for index in indices],
            text_pages=text_pages,
            image_pages=image_pages,
            low_text_pages=low_text_pages,
            average_text_chars=round(
                sum(character_counts) / sample_count if sample_count else 0.0, 2
            ),
            native_text_ratio=round(native_ratio, 4),
        )
    finally:
        document.close()


def _sample_indices(page_count: int, limit: int) -> list[int]:
    if page_count <= 0 or limit <= 0:
        return []
    if page_count <= limit:
        return list(range(page_count))
    if limit == 1:
        return [0]
    return sorted(
        {round(position * (page_count - 1) / (limit - 1)) for position in range(limit)}
    )

