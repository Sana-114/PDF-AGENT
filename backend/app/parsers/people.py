"""Conservative, source-line based extraction of academic front matter."""

import re

from app.parsers.layout import ABSTRACT_PREFIX, AFFILIATION_HINT, RawTextBlock

ORGANIZATION = re.compile(
    r"\b(?:research|OpenAI|Google|Microsoft|INRIA|Nuxeo|UC Berkeley|Universit|"
    r"Neurospin|CEA|InstaDeep|NVIDIA|Technical University|lab|UMass|Total SA)\b",
    re.I,
)
NON_NAME = re.compile(
    r"\b(?:received|accepted|published|updates|equal|contribution|advising|"
    r"editor|copyright|rights|reserved|journal|proceedings|email|correspondence)\b",
    re.I,
)


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
    normalized_title = " ".join((title or "").casefold().split())
    for page in raw_pages[:3]:
        all_lines = [line for block in page for line in block.lines]
        emails = [line for line in all_lines if "@" in line["text"]]
        email_rows = [
            line["bbox"][3]
            for line in emails
            if any(
                "@" not in other["text"]
                and abs(line["bbox"][3] - other["bbox"][3]) < 3
                and other["bbox"][2] < line["bbox"][0]
                for other in all_lines
            )
        ]
        # Numbered affiliations may appear below an unlabelled abstract.
        for line in all_lines:
            if re.match(r"^\d\D", line["text"]) and (
                AFFILIATION_HINT.search(line["text"]) or ORGANIZATION.search(line["text"])
            ):
                affiliations.append(line["text"].split("e-mail:")[0].strip())
        for block in sorted(page, key=lambda b: (b.bbox[1], b.bbox[0])):
            value = " ".join(block.text.casefold().split())
            if not active:
                if normalized_title and normalized_title in value:
                    active = True
                continue
            if ABSTRACT_PREFIX.match(block.text) or re.match(
                r"^(?:1\.?\s+Introduction|Editor:)", block.text, re.I
            ):
                return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))
            if block.bbox[3] - block.bbox[1] > (block.bbox[2] - block.bbox[0]) * 1.5:
                continue
            # An unlabelled abstract (Nature) still provides a safe boundary.
            if len(block.text) > 450 and "@" not in block.text and block.text.count(",") < 5:
                return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))
            for line in block.lines or [{"text": block.text}]:
                text = line["text"].strip()
                if "@" in text or NON_NAME.search(text):
                    continue
                if AFFILIATION_HINT.search(text) or ORGANIZATION.search(text):
                    affiliations.append(text)
                    continue
                if re.match(r"^\d{2,}\b", text) or len(text) > 240:
                    continue
                if len(email_rows) >= 2 and not any(
                    abs(line.get("bbox", block.bbox)[3] - y) < 3 for y in email_rows
                ):
                    continue
                if text.isupper():
                    continue
                cleaned = re.sub(r"[\d*∗†‡]+", "", text)
                for part in re.split(r"\s*(?:[,;、，&]|\band\b)\s*", cleaned):
                    tokens = part.split()
                    if not 2 <= len(tokens) <= 5:
                        continue
                    if all(
                        t.lstrip("´`¨^")[0].isupper() or t in {"de", "del", "van", "von", "da"}
                        for t in tokens
                        if t.lstrip("´`¨^")
                    ):
                        authors.append(" ".join(tokens))
    return list(dict.fromkeys(authors)), list(dict.fromkeys(affiliations))
