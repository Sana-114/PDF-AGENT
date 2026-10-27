import asyncio
import re
from collections.abc import Sequence

from app.core.config import Settings, settings
from app.schemas.discovery import (
    PaperCandidate,
    ReferenceCandidateMatch,
    ReferenceResolution,
)
from app.services.paper_discovery import (
    PaperDiscoveryError,
    PaperDiscoveryService,
    detect_query_kind,
    paper_discovery,
)

TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
LEADING_LABEL_PATTERN = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s*")
VENUE_START = re.compile(r"^(?:in\b|proceedings\b|journal\b|arxiv\b|https?://|vol\.|pp\.)", re.I)


def _reference_title(clean: str) -> str | None:
    """Extract only a confident quoted or author-period-title bibliography title.

    This is query cleanup, not an identity assertion. Unfamiliar layouts retain
    their original text; all returned candidates still need independent scoring.
    """
    quoted = re.search(r'["“]([^"”]{12,})["”]', clean)
    if quoted and len(_tokens(quoted[1])) >= 4:
        return quoted[1].strip(" .,;")
    parts = re.split(r"(?<=[.!?])\s+", clean)
    joiners = {"and", "et", "al", "van", "von", "de", "der", "del", "da", "jr"}
    for index, part in enumerate(parts):
        if index == 0:
            continue
        prefix = " ".join(parts[:index])
        # Initials, surnames and an optional publication year are plausible
        # author prefixes. Do not guess when prose already precedes the title.
        words = re.findall(r"[^\W\d_]+", YEAR_PATTERN.sub("", prefix), re.UNICODE)
        if not words or any(
            not word[0].isupper() and word.lower() not in joiners for word in words
        ):
            continue
        title = re.sub(r"^\(?(?:19|20)\d{2}\)?[.,:]?\s+", "", part).rstrip(".").strip()
        if len(_tokens(title)) >= 4 and not VENUE_START.search(title):
            return title
    return None


def reference_search_query(reference_text: str) -> str:
    """Keep identifiers exact and otherwise clean a bibliography entry for title search."""
    clean = " ".join(reference_text.split())
    query_kind, normalized = detect_query_kind(clean)
    if query_kind in {"doi", "arxiv"}:
        return normalized
    clean = LEADING_LABEL_PATTERN.sub("", clean)
    return (_reference_title(clean) or clean)[:500]


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_PATTERN.findall(value)
        if len(token) > 1 or "\u3400" <= token <= "\u9fff"
    }


def score_reference_candidate(reference_text: str, candidate: PaperCandidate) -> tuple[float, str]:
    reference_lower = " ".join(reference_text.lower().split())
    if candidate.doi and candidate.doi.lower() in reference_lower:
        return 1.0, "DOI 完全一致"
    if candidate.arxiv_id and candidate.arxiv_id.lower() in reference_lower:
        return 1.0, "arXiv ID 完全一致"

    cleaned = LEADING_LABEL_PATTERN.sub("", " ".join(reference_text.split()))
    extracted = _reference_title(cleaned)
    reference_tokens = _tokens(extracted or cleaned)
    title_tokens = _tokens(candidate.title)
    overlap = len(reference_tokens & title_tokens)
    title_coverage = overlap / len(title_tokens) if title_tokens else 0.0
    reference_coverage = overlap / len(reference_tokens) if reference_tokens else 0.0
    # A short generic candidate can cover its own few words while omitting most
    # of the actual title. Require both directions when a title was isolated;
    # otherwise use Dice overlap to tolerate author/venue text without that bias.
    title_similarity = (
        min(title_coverage, reference_coverage)
        if extracted
        else 2 * overlap / max(1, len(reference_tokens) + len(title_tokens))
    )

    surnames = {
        parts[-1].lower()
        for author in candidate.authors
        if (parts := TOKEN_PATTERN.findall(author))
    }
    author_coverage = len(_tokens(reference_text) & surnames) / len(surnames) if surnames else 0.0
    reference_years = set(YEAR_PATTERN.findall(reference_text))
    year_match = 1.0 if candidate.year and str(candidate.year) in reference_years else 0.0
    score = min(1.0, 0.78 * title_similarity + 0.14 * author_coverage + 0.08 * year_match)
    reason = (
        f"题名词覆盖 {title_coverage:.0%}，条目覆盖 {reference_coverage:.0%}，"
        f"作者匹配 {author_coverage:.0%}，"
        f"年份{'一致' if year_match else '未确认'}"
    )
    return round(score, 4), reason


class ReferenceDiscoveryService:
    def __init__(
        self,
        discovery: PaperDiscoveryService = paper_discovery,
        config: Settings = settings,
    ) -> None:
        self.discovery = discovery
        self.config = config

    async def resolve_many(
        self,
        references: Sequence[dict],
        *,
        candidates_per_reference: int = 3,
    ) -> list[ReferenceResolution]:
        semaphore = asyncio.Semaphore(self.config.reference_discovery_concurrency)

        async def bounded(reference: dict) -> ReferenceResolution:
            async with semaphore:
                return await self.resolve_one(reference, candidates_per_reference)

        return list(await asyncio.gather(*(bounded(reference) for reference in references)))

    async def resolve_one(
        self, reference: dict, candidates_per_reference: int = 3
    ) -> ReferenceResolution:
        reference_id = str(reference.get("reference_id") or "")
        label = str(reference.get("label") or "")
        text = str(reference.get("text") or "").strip()
        page_number = max(1, int(reference.get("page_number") or 1))
        query = reference_search_query(text)
        if not query:
            return ReferenceResolution(
                reference_id=reference_id,
                label=label,
                text=text,
                page_number=page_number,
                status="not_found",
                warnings=["参考文献条目没有可检索文本。"],
            )
        try:
            query_kind, candidates, warnings = await self.discovery.search(
                query, candidates_per_reference
            )
        except PaperDiscoveryError as exc:
            return ReferenceResolution(
                reference_id=reference_id,
                label=label,
                text=text,
                page_number=page_number,
                status="error",
                warnings=[str(exc)],
            )

        matches = [
            ReferenceCandidateMatch(
                paper=candidate,
                match_score=score,
                match_reason=reason,
            )
            for candidate in candidates
            for score, reason in [score_reference_candidate(text, candidate)]
        ]
        matches.sort(key=lambda item: item.match_score, reverse=True)
        if not matches:
            status = "not_found"
        elif matches[0].match_score >= self.config.reference_match_threshold:
            status = "matched"
        else:
            status = "uncertain"
        return ReferenceResolution(
            reference_id=reference_id,
            label=label,
            text=text,
            page_number=page_number,
            status=status,
            query_kind=query_kind,
            candidates=matches,
            warnings=warnings,
        )


reference_discovery = ReferenceDiscoveryService()
