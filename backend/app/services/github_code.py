from dataclasses import dataclass
from math import ceil

import httpx

from app.core.config import Settings, settings
from app.schemas.discovery import PaperCandidate
from app.services.text_features import tokenize


class GitHubCodeSearchError(RuntimeError):
    pass


@dataclass(slots=True)
class GitHubRepositoryMatch:
    url: str
    full_name: str
    stars: int
    score: float


class GitHubCodeSearchService:
    def __init__(
        self,
        config: Settings = settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.client = client

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.github_api_version,
            "User-Agent": "PaperPilot/0.2 research-code-discovery",
        }
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        return headers

    async def find_repository(
        self, candidate: PaperCandidate
    ) -> GitHubRepositoryMatch | None:
        if candidate.code_url:
            return GitHubRepositoryMatch(
                url=candidate.code_url,
                full_name=candidate.code_url.removeprefix("https://github.com/"),
                stars=candidate.code_stars or 0,
                score=1.0,
            )
        if not self.config.github_code_search_enabled:
            return None

        url = f"{self.config.github_api_base_url.rstrip('/')}/search/repositories"
        query = f'"{candidate.title[:240]}" in:name,description,readme'
        try:
            if self.client is not None:
                response = await self.client.get(
                    url,
                    params={"q": query, "per_page": 5, "sort": "stars", "order": "desc"},
                    headers=self._headers(),
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.config.scholarly_api_timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(
                        url,
                        params={
                            "q": query,
                            "per_page": 5,
                            "sort": "stars",
                            "order": "desc",
                        },
                        headers=self._headers(),
                    )
            if response.status_code in {403, 429}:
                raise GitHubCodeSearchError("GitHub 搜索达到速率限制。")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubCodeSearchError(f"GitHub 代码检索失败：{exc}") from exc

        title_tokens = set(tokenize(candidate.title))
        best: GitHubRepositoryMatch | None = None
        for item in payload.get("items", []):
            searchable = f"{item.get('name', '')} {item.get('description') or ''}"
            repository_tokens = set(tokenize(searchable))
            overlap = len(title_tokens & repository_tokens)
            coverage = (
                overlap / len(title_tokens) if title_tokens else 0.0
            )
            match = GitHubRepositoryMatch(
                url=str(item.get("html_url") or ""),
                full_name=str(item.get("full_name") or ""),
                stars=int(item.get("stargazers_count") or 0),
                score=round(coverage, 4),
            )
            enough_title_terms = overlap >= max(2, ceil(len(title_tokens) * 0.6))
            if match.url.startswith("https://github.com/") and enough_title_terms and (
                best is None or match.score > best.score
            ):
                best = match
        return best if best and best.score >= 0.6 else None


github_code_search = GitHubCodeSearchService()
