"""Conservative, source-line based extraction of academic front matter."""

import re
import unicodedata

from app.parsers.layout import AFFILIATION_HINT, AUTHOR_WITH_MARKER, RawTextBlock

ORGANIZATION = re.compile(
    r"\b(?:research|OpenAI|Google|Microsoft|INRIA|Nuxeo|UC Berkeley|Universit|"
    r"Neurospin|CEA|InstaDeep|NVIDIA|Technical University|lab|UMass|Total SA|GenAI|Meta)\b",
    re.I,
)
NON_NAME = re.compile(
    r"\b(?:received|accepted|published|updates|equal|contribution|advising|"
    r"editor|copyright|rights|reserved|journal|proceedings|email|correspondence|"
    r"date|website|version|contributors?|detailed|work performed)\b",
    re.I,
)
NUMBERED_AUTHOR_BOUNDARY = re.compile(r"(?<=\w)\s*[*∗†‡]*\s*\d+\s+(?=[A-Z][a-z])")
FRONT_MATTER_END = re.compile(
    r"^\s*(?:(?:[§\d]+(?:\.\d+)*\.?\s+)?(?:abstract|摘要)\b|"
    r"1\.?\s+Introduction\b|Editor:)",
    re.I,
)
FOOTNOTES = r"[\d*∗†‡⋄♦§¶♠♣♥♢]+"
# OCR may render the stem of a dagger as a bar after another genuine
# footnote marker. Limit cleanup to that marker cluster, never bare/name bars.
CREDIT_MARKERS = FOOTNOTES + r"(?:[|｜]+)?"
EMAIL = re.compile(r"@\s*[\w.-]+\s*\.\s*[A-Za-z]{2,}")
PARTICLES = {"de", "del", "van", "von", "da", "das", "dos", "las", "la", "di", "den"}
COLLECTIVE = re.compile(r"^(?:[\w-]+(?:\s+[\w-]+){0,3}\s+(?:Team|Workshop)\b.*|"
                        r"OpenAI|DeepSeek-AI)$", re.I)


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _credit(text: str) -> str:
    # NFKC would turn a detached acute accent into a separate combining token.
    text = "".join(unicodedata.normalize("NFKC", c) if "\ufb00" <= c <= "\ufb06" else c
                   for c in text)
    return re.sub(CREDIT_MARKERS + r"B?", "", text).strip()


def _affiliation_line(text: str) -> str | None:
    """Keep an institutional source line; never infer one from an e-mail."""
    text = text.strip()
    if "@" in text or NON_NAME.search(text):
        return None
    text = re.sub(r"^" + FOOTNOTES + r"\s*", "", text)
    if len(text.split()) < 2 and not ORGANIZATION.search(text):
        return None
    if AFFILIATION_HINT.search(text) or ORGANIZATION.search(text):
        return text
    return None


def _unmarked_row_names(text: str) -> list[str]:
    """Only accept name-shaped segments on an independently verified author row."""
    names = []
    segments = [part for numbered in NUMBERED_AUTHOR_BOUNDARY.split(text)
                for part in re.split(CREDIT_MARKERS + r"B?\s+(?=[A-ZÀ-ÖØ-ÞŁ])", numbered)]
    for segment in segments:
        cleaned = _credit(segment)
        for part in re.split(r"\s*(?:[,;、，&]|\band\b)\s*", cleaned):
            tokens = part.split()
            if 2 <= len(tokens) <= 7 and all(
                token.lstrip("´`¨^")[0].isupper()
                or token in PARTICLES
                for token in tokens
                if token.lstrip("´`¨^")
            ):
                names.append(" ".join(tokens))
    return names


def extract_front_matter_people(
    raw_pages: list[list[RawTextBlock]],
    title: str | None,
) -> tuple[list[str], list[str]]:
    """Use at most three pages, stopping at the abstract or first body paragraph.

    Lines preserve independently positioned names in author grids. Uncertain
    body prose and postal addresses are never guessed to be person names.
    """
    authors: list[str] = []
    affiliations: list[str] = []
    active = False
    normalized_title = _normal(title or "")
    for page in raw_pages[:3]:
        all_lines = [line for block in page for line in block.lines]
        cutoff = min((b.bbox[1] for b in page if FRONT_MATTER_END.match(b.text)),
                     default=float("inf"))
        emails = [line for line in all_lines if EMAIL.search(line["text"])
                  and line["bbox"][1] < cutoff]
        email_rows = [
            line["bbox"][3]
            for line in emails
            if any(
                "@" not in other["text"]
                and _unmarked_row_names(other["text"])
                and not _affiliation_line(other["text"])
                and not NON_NAME.search(other["text"])
                and abs(line["bbox"][3] - other["bbox"][3]) < 3
                and other["bbox"][2] < line["bbox"][0]
                for other in all_lines
            )
        ]
        # Numbered affiliations may appear below an unlabelled abstract.
        for line in all_lines:
            if re.match(r"^\d+\s*(?=[A-Z])", line["text"]):
                affiliation_text = _affiliation_line(line["text"])
                if affiliation_text:
                    affiliations.append(affiliation_text)
        for block in sorted(page, key=lambda b: (b.bbox[1], b.bbox[0])):
            value = _normal(block.text)
            if not active:
                if normalized_title and normalized_title in value:
                    active = True
                continue
            if FRONT_MATTER_END.match(block.text):
                return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))
            credit = _credit(block.text)
            if not authors and COLLECTIVE.fullmatch(credit) and len(credit) <= 100:
                authors.append(credit)
                continue
            marked_names = [
                match.group("name") for match in AUTHOR_WITH_MARKER.finditer(block.text)
            ]
            if marked_names and "@" in block.text:
                # OCR may merge the entire author grid, affiliations and e-mails
                # into a single block, including a single author in its last row.
                # Extract independent source lines before discarding the grid;
                # the whole merged text is not a valid affiliation string.
                if block.lines:
                    marked_rows = [
                        line["bbox"][3]
                        for line in block.lines
                        if "bbox" in line and AUTHOR_WITH_MARKER.search(line["text"])
                    ]
                    before_affiliation = True
                    for line in block.lines:
                        text = line["text"].strip()
                        if FRONT_MATTER_END.match(text):
                            return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))
                        affiliation_text = _affiliation_line(text)
                        if affiliation_text:
                            affiliations.append(affiliation_text)
                            before_affiliation = False
                            continue
                        if "@" in text or NON_NAME.search(text):
                            continue
                        line_names = [
                            match.group("name") for match in AUTHOR_WITH_MARKER.finditer(text)
                        ]
                        if line_names:
                            authors.extend(_line_names(line) or line_names)
                        elif before_affiliation or ("bbox" in line and any(
                            abs(line["bbox"][3] - y) < 3 for y in marked_rows
                        )):
                            # Equal-contribution stars often mark only some of
                            # the authors. Keep unstarred names on the same row,
                            # without turning a postal-address row into names.
                            authors.extend(_line_names(line))
                else:
                    authors.extend(marked_names)
                continue
            if block.bbox[3] - block.bbox[1] > (block.bbox[2] - block.bbox[0]) * 1.5:
                continue
            # An unlabelled abstract (Nature) still provides a safe boundary.
            if (len(block.text) > 450 and "@" not in block.text
                    and not _all_name_lines(block) and block.text.count(",") < 5):
                return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))
            for line in block.lines or [{"text": block.text}]:
                text = line["text"].strip()
                if "@" in text or NON_NAME.search(text) or "http" in text:
                    continue
                if len(text.split()) == 1 and not ORGANIZATION.search(text):
                    # A book's copyright paragraph may end with a lone word such
                    # as "Department."; it is not an institutional affiliation.
                    continue
                affiliation_text = _affiliation_line(text)
                if affiliation_text:
                    affiliations.append(affiliation_text)
                    continue
                if re.match(r"^\d{2,}\b", text) or len(text) > 240:
                    continue
                if len(email_rows) >= 2 and not any(
                    abs(line.get("bbox", block.bbox)[3] - y) < 3 for y in email_rows
                ):
                    continue
                if text.isupper():
                    continue
                authors.extend(_line_names(line))
    return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))


def _line_names(line: dict) -> list[str]:
    # Explicit affiliation markers are stronger boundaries than spacing alone.
    if re.search(CREDIT_MARKERS + r"B?\s+(?=[A-ZÀ-ÖØ-ÞŁ])", line["text"]):
        return _unmarked_row_names(line["text"])
    return [name for part in line.get("people_parts", [line["text"]])
            for name in _unmarked_row_names(part)]


def _all_name_lines(block: RawTextBlock) -> bool:
    return bool(block.lines) and all(_line_names(line) for line in block.lines)


def enrich_people_geometry(blocks: list[RawTextBlock], raw_dict: dict) -> None:
    """Keep compact glyph-derived name segments, never the full glyph payload.

    Wide source gaps separate author names; ordinary word gaps recover missing
    spaces after ligatures. Superscript affiliation glyphs are not name letters.
    Original block/line text and evidence coordinates are left intact.
    """
    source_lines = {}
    for block in raw_dict.get("blocks", []):
        for line in block.get("lines", []):
            if tuple(line.get("dir", (1, 0))) != (1, 0):
                continue
            spans = line.get("spans", [])
            size = max((float(s.get("size", 0)) for s in spans), default=0)
            if not size:
                continue
            pieces = [""]
            previous = None
            for span in spans:
                if float(span.get("size", size)) < size * 0.8:
                    continue
                for char in span.get("chars", []):
                    value, box = char["c"], char["bbox"]
                    gap = box[0] - previous[2] if previous else 0
                    if value.isspace() and box[2] - box[0] > size * 0.48:
                        if pieces[-1].strip():
                            pieces.append("")
                        previous = box
                        continue
                    if gap > size * 0.48 and pieces[-1].strip():
                        pieces.append("")
                    elif gap > size * 0.20 and pieces[-1] and not pieces[-1].endswith(" "):
                        pieces[-1] += " "
                    pieces[-1] += value
                    previous = box
            key = tuple(round(float(v), 2) for v in line["bbox"])
            source_lines[key] = [p.strip() for p in pieces if p.strip()]
    for block in blocks:
        for line in block.lines:
            parts = source_lines.get(tuple(line["bbox"]))
            if parts:
                line["people_parts"] = parts
