"""Conservative author-year resolution against the document's own bibliography.

No online metadata or model guesses: ambiguous author/year keys remain unlinked.
Offsets always refer to the unchanged body block, including line breaks.
"""

import re
import unicodedata
from collections import defaultdict

NAME = r"[A-ZÀ-ÖØ-Þ][\w’'\-]+"
YEAR = r"(?:19|20)\d{2}[a-z]?"
CITATION = re.compile(
    rf"(?<![\w’'-])(?P<first>{NAME})"
    rf"(?:(?P<etal>\s+et\s+al\.?)|\s+(?:and|&)\s+(?P<second>{NAME}))?"
    rf"(?:\s*,\s*|\s+\(\s*|\s+)"
    rf"(?P<year>{YEAR})(?![\w])"
    rf"(?P<more>(?:\s*,\s*{YEAR}(?![\w]))*)"
)


def _normal(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("’", "'").casefold()


def bibliography_keys(references: list[dict]) -> dict[tuple[str, str], list[dict]]:
    keys: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ref in references:
        text = str(ref.get("text") or "")
        years = set(re.findall(rf"\b({YEAR})\b", text))
        # Two distinct dates may describe a reprint or a different cited work.
        if len(years) != 1:
            continue
        year = next(iter(years))
        author_prefix = re.split(r"(?<=[a-z])\.\s+(?=[A-Z])", text, maxsplit=1)[0]
        author_prefix = re.split(rf"\b{YEAR}\b", author_prefix, maxsplit=1)[0]
        first = re.split(r",|;|\s+and\s+|\s+&\s+", author_prefix, maxsplit=1)[0]
        words = re.findall(NAME, first)
        words = [word for word in words if len(word) > 1]
        if not words:
            continue
        surname = words[-1]
        keys[_normal(surname), year].append({
            "label": str(ref["label"]), "authors": _normal(author_prefix),
        })
    return keys


def author_year_mentions(text: str, keys: dict) -> list[dict]:
    mentions = []
    for match in CITATION.finditer(text):
        for year in [match["year"]]:
            candidates = keys.get((_normal(match["first"]), year), [])
            if match["second"]:
                second = _normal(match["second"])
                candidates = [c for c in candidates if re.search(
                    rf"(?<!\w){re.escape(second)}(?!\w)", c["authors"]
                )]
            # Never choose the first entry when the bibliography is ambiguous.
            if len(candidates) != 1:
                continue
            start, end = match.span()
            if match["more"]:
                end = match.end("year")
            mentions.append({"label": candidates[0]["label"], "start_offset": start,
                             "end_offset": end, "citation_text": text[start:end]})
    return mentions
