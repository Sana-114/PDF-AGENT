"""Keep collective authorship separate from front-matter contribution groups.

Activation requires an explicit collective credit followed by a contributor
heading before the abstract. Never search the body, bibliography or filename.
"""

import re

from app.parsers.base import ContributorGroup, ContributorSource, Page
from app.parsers.layout import RawTextBlock
from app.parsers.people import COLLECTIVE, FRONT_MATTER_END, NON_NAME, _credit, _normal

GROUP_START = re.compile(r"\bcontributors?\b", re.I)
ROLE = re.compile(r"^[^\W\d_][\w &/\-]{1,75}$", re.UNICODE)
BODY_END = re.compile(
    r"^\s*(?:\d+[.)]?\s*)?(?:abstract|introduction|references|bibliography|acknowledgements)\b",
    re.I,
)


def _members(lines: list[str]) -> list[str]:
    # Join only explicit line wrapping. Do not guess accents, expand initials,
    # invent institutions, or collapse the same person across different roles.
    text = re.sub(r"(?<=\w)-\n(?=[a-zà-öø-ÿ])", "", "\n".join(lines))
    text = re.sub(r"(?<=\w)-\n(?=\w)", "-", text)
    text = " ".join(text.split())
    return list(
        dict.fromkeys(
            value.strip()
            for value in re.split(r"[,;]", text)
            if 2 <= len(value.strip().split()) <= 8
            and not re.fullmatch(r".*\b[A-Z]\.", value.strip())
        )
    )


def extract_contributor_groups(
    raw_pages: list[list[RawTextBlock]],
    pages: list[Page],
    title: str | None,
) -> tuple[str | None, list[ContributorGroup]]:
    collective = None
    active_title = False
    groups: list[ContributorGroup] = []
    current_role = None
    sources: list[ContributorSource] = []
    body_lines: list[str] = []
    stop = False

    def finish():
        members = _members(body_lines)
        if current_role and sources and len(members) >= 2:
            groups.append(ContributorGroup(current_role, members, list(sources)))

    for raw_page, page in zip(raw_pages[:3], pages[:3], strict=False):
        for block in sorted(raw_page, key=lambda b: (b.bbox[1], b.bbox[0])):
            value = _normal(block.text)
            if not active_title:
                if title and _normal(title) in value:
                    active_title = True
                continue
            if FRONT_MATTER_END.match(block.text) or BODY_END.match(block.text):
                stop = True
                break
            credit = _credit(block.text)
            if COLLECTIVE.fullmatch(credit) and len(credit) <= 100:
                collective = collective or credit
                continue
            lines = [str(line["text"]).strip() for line in block.lines if line.get("text")]
            if not collective or not lines:
                continue
            heading = lines[0]
            # 'Contributors' is a NON_NAME marker in ordinary author rows;
            # it is valid here only as the explicit contribution heading.
            if "@" in block.text or (
                NON_NAME.search(block.text) and not GROUP_START.search(heading)
            ):
                continue
            new_role = (
                len(lines) > 1
                and bool(ROLE.fullmatch(heading))
                and len(heading.split()) <= 7
                and "," in " ".join(lines[1:])
            )
            if current_role is None and not (new_role and GROUP_START.search(heading)):
                continue
            if not new_role:
                if not sources or page.page_number != sources[-1].page_number + 1:
                    continue
                if block.bbox[1] > page.height * 0.35 or "," not in block.text:
                    continue
            match = next(
                (b for b in page.blocks if b.bbox == block.bbox and b.text == block.text), None
            )
            if match is None:
                # Never create provenance by synthesizing block IDs.
                return None, []
            if new_role:
                finish()
                current_role, sources, body_lines = heading, [], []
            sources.append(
                ContributorSource(page.page_number, match.block_id, list(match.bbox), match.text)
            )
            body_lines.extend(lines[1:] if new_role else lines)
        if stop:
            break
    finish()
    if not stop and len(raw_pages) > 3:
        # Do not call a page-limited prefix the complete contribution listing.
        return None, []
    return (collective, groups) if groups else (None, [])
