"""Conservative publication-year fallback from a first-page proceedings footer.

Do not infer dates from filenames, bibliography years or PDF creation dates.
Return a literal, positioned source so the recovered metadata remains auditable.
"""
import re

YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
ARXIV = re.compile(r"(?P<year>\d{2})(?P<month>0[1-9]|1[0-2])\.\d{4,5}(?:v[1-9]\d*)?")


def resolve_publication_year(arxiv_id, metadata, parsed=None):
    """Return (year, optional literal footer) with the same priority everywhere.

    External bibliographic metadata and valid arXiv identity take precedence.
    A footer only fills an unknown year; it cannot replace a known preprint date.
    """
    if isinstance(metadata, dict):
        for key in ("year", "published_at"):
            value = metadata.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                match = YEAR.search(str(value))
                if match:
                    return int(match.group()), None
    identity = arxiv_id or (metadata.get("arxiv_id") if isinstance(metadata, dict) else None)
    match = ARXIV.fullmatch(identity) if isinstance(identity, str) else None
    if match:
        return 2000 + int(match["year"]), None
    imprint = publication_footer(parsed or {})
    return (imprint["year"], imprint) if imprint else (None, None)

PROCEEDINGS = re.compile(
    r"^Proceedings of (?P<venue>.{4,180}?)\s+(?P<year>(?:19|20)\d{2}),\s*"
    r"pages?\s+\d+\s*[-–—]\s*\d+\b", re.I,
)


def publication_footer(parsed: dict) -> dict | None:
    pages = parsed.get("pages", [])
    if not pages or pages[0].get("page_number") != 1:
        return None
    page = pages[0]
    height = page.get("height")
    if not isinstance(height, (int, float)) or height <= 0:
        return None
    candidates = []
    for block in page.get("blocks", []):
        box, text = block.get("bbox"), block.get("text", "")
        if (not isinstance(box, list) or len(box) != 4 or not block.get("block_id")
                or not all(isinstance(n, (int, float)) for n in box)
                or not (0 <= box[1] <= box[3] <= height + 2 and box[1] >= height * .85)):
            continue
        match = PROCEEDINGS.match(" ".join(text.split()))
        if match is None:
            continue
        year = int(match['year'])
        # Require corroboration in this same imprint (e.g. conference date or copyright).
        years = re.findall(r"\b(?:19|20)\d{2}\b", text)
        if len(years) < 2 or any(int(value) != year for value in years):
            continue
        candidates.append({"year": year, "venue": match['venue'].strip(),
            "page_number": 1, "block_id": block['block_id'], "bbox": box, "quote": text})
    return candidates[0] if len(candidates) == 1 else None
