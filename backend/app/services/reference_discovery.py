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


def reference_search_query(reference_text: str) -> str:
    """Keep identifiers exact and otherwise clean a bibliography entry for title search."""
    clean = " ".join(reference_text.split())
    query_kind, normalized = detect_query_kind(clean)
    if query_kind in {"doi", "arxiv"}:
        return normalized
    return LEADING_LABEL_PATTERN.sub("", clean)[:500]


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_PATTERN.findall(value)
        if len(token) > 1 or "\u3400" <= token <= "\u9fff"
    }


def score_reference_candidate(
    reference_text: str, candidate: PaperCandidate
) -> tuple[float, str]:
    reference_lower = " ".join(reference_text.lower().split())
    title_lower = " ".join(candidate.title.lower().split())
    if candidate.doi and candidate.doi.lower() in reference_lower:
        return 1.0, "DOI 完全一致"
    if candidate.arxiv_id and candidate.arxiv_id.lower() in reference_lower:
        return 1.0, "arXiv ID 完全一致"

    reference_tokens = _tokens(reference_text)
    title_tokens = _tokens(candidate.title)
    title_coverage = (
        len(reference_tokens & title_tokens) / len(title_tokens) if title_tokens else 0.0
    )
    if title_lower and title_lower in reference_lower:
        title_coverage = 1.0

    surnames = {
        parts[-1].lower()
        for author in candidate.authors
        if (parts := TOKEN_PATTERN.findall(author))
    }
    author_coverage = (
        len(reference_tokens & surnames) / len(surnames) if surnames else 0.0
    )
    reference_years = set(YEAR_PATTERN.findall(reference_text))
    year_match = 1.0 if candidate.year and str(candidate.year) in reference_years else 0.0
    score = min(1.0, 0.78 * title_coverage + 0.14 * author_coverage + 0.08 * year_match)
    reason = (
        f"题名词覆盖 {title_coverage:.0%}，作者匹配 {author_coverage:.0%}，"
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
