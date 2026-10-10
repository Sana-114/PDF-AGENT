"""Bounded source-image OCR for author grids, never a name lookup or gold repair."""

from dataclasses import replace
from difflib import SequenceMatcher

import fitz

from app.parsers.layout import AUTHOR_WITH_MARKER, RawTextBlock, union_bbox
from app.parsers.people import extract_front_matter_people


def author_grid_region(block: RawTextBlock, page_rect: fitz.Rect) -> fitz.Rect | None:
    emails = [line for line in block.lines if "@" in line["text"]]
    names = [line for line in block.lines if AUTHOR_WITH_MARKER.search(line["text"])]
    if len(emails) < 2 or len(names) < 2:
        return None
    boxes = [line["bbox"] for line in [*emails, *names] if "bbox" in line]
    if not boxes:
        return None
    region = (fitz.Rect(union_bbox(boxes)) + (-6, -6, 6, 6)) & page_rect
    # Only compact first-page front matter; never crop a body-sized merged block.
    if region.is_empty or region.height > page_rect.height * 0.4:
        return None
    if region.y1 > page_rect.height * 0.65:
        return None
    return region


def project_crop_blocks(
    blocks: list[RawTextBlock], *, origin: tuple[float, float], scale: tuple[float, float],
) -> list[RawTextBlock]:
    """Map PDF OCR crop coordinates back onto the unchanged original page."""
    def project(box):
        return [round(origin[index % 2] + value * scale[index % 2], 2)
                for index, value in enumerate(box)]

    return [replace(block, bbox=project(block.bbox),
                    font_size=block.font_size * scale[1],
                    lines=[{**line, "bbox": project(line["bbox"])} for line in block.lines])
            for block in blocks]


def compatible_people(
    original: list[RawTextBlock], candidate: list[RawTextBlock], title: str,
) -> bool:
    old_names, old_affiliations = extract_front_matter_people([original], title)
    new_names, new_affiliations = extract_front_matter_people([candidate], title)
    if len(old_names) < 2 or len(new_names) != len(old_names):
        return False
    # Keep order and require close, independently observed source spellings.
    # Do not silently add a person or transfer an affiliation from another paper.
    if any(SequenceMatcher(None, old.casefold(), new.casefold()).ratio() < 0.85
           for old, new in zip(old_names, new_names, strict=True)):
        return False
    if not {name.casefold() for name in old_affiliations}.issubset(
        {name.casefold() for name in new_affiliations}
    ):
        return False
    old_marks = sum(len(AUTHOR_WITH_MARKER.findall(block.text)) for block in original)
    new_marks = sum(len(AUTHOR_WITH_MARKER.findall(block.text)) for block in candidate)
    return new_marks >= old_marks
