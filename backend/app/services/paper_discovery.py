import re
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import httpx

from app.core.config import Settings, settings
from app.schemas.discovery import PaperCandidate, QueryKind
from app.services.storage import StagedUpload, storage

SEMANTIC_FIELDS = ",".join(
    (
        "paperId",
        "title",
        "abstract",
        "authors",
        "year",
        "publicationDate",
        "externalIds",
        "citationCount",
        "influentialCitationCount",
        "openAccessPdf",
        "url",
        "venue",
    )
)
ARXIV_ID_PATTERN = (
    r"(?P<identifier>(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})"
    r"(?:v(?P<version>\d+))?)"
)
ARXIV_PATTERN = re.compile(
    rf"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?{ARXIV_ID_PATTERN}(?:\.pdf)?",
    re.IGNORECASE,
)
ARXIV_BARE_PATTERN = re.compile(rf"{ARXIV_ID_PATTERN}(?:\.pdf)?", re.IGNORECASE)
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")


class PaperDiscoveryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(TAG_PATTERN.sub(" ", value)).split())


def _extract_arxiv_id(value: str) -> tuple[str, int | None] | None:
    match = ARXIV_PATTERN.search(value.strip())
    if match is None:
        return None
    identifier = match.group("identifier")
    version = int(match.group("version")) if match.group("version") else None
    base = re.sub(r"v\d+$", "", identifier, flags=re.IGNORECASE)
    return base, version


def _extract_doi(value: str) -> str | None:
    match = DOI_PATTERN.search(value.strip())
    if match is None:
        return None
    return match.group(0).rstrip(".,;:)]}").lower()


def detect_query_kind(query: str) -> tuple[QueryKind, str]:
    clean = " ".join(query.split())
    doi = _extract_doi(clean)
    if doi:
        return "doi", doi
    explicit_arxiv = clean.lower().startswith("arxiv:") or "arxiv.org/" in clean.lower()
    arxiv = _extract_arxiv_id(clean) if explicit_arxiv else None
    if not explicit_arxiv:
        bare_match = ARXIV_BARE_PATTERN.fullmatch(clean)
        if bare_match:
            base = re.sub(r"v\d+$", "", bare_match.group("identifier"), flags=re.IGNORECASE)
            version = int(bare_match.group("version")) if bare_match.group("version") else None
            arxiv = base, version
    if arxiv:
        base, version = arxiv
        return "arxiv", f"{base}v{version}" if version else base
    return "title", clean


class PaperDiscoveryService:
    def __init__(
        self,
        config: Settings = settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.client = client

    @property
    def allowed_pdf_hosts(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.config.scholarly_pdf_hosts.split(",")
            if host.strip()
        }

    def _headers(self, provider: str) -> dict[str, str]:
        headers = {"User-Agent": "PaperPilot/0.2 scholarly-paper-import"}
        if provider == "semantic_scholar" and self.config.semantic_scholar_api_key:
            headers["x-api-key"] = self.config.semantic_scholar_api_key
        if provider == "crossref" and self.config.scholarly_contact_email:
            headers["User-Agent"] += f" (mailto:{self.config.scholarly_contact_email})"
        return headers

    async def _get(self, url: str, *, provider: str, params: dict | None = None) -> httpx.Response:
        try:
            if self.client is not None:
                return await self.client.get(url, params=params, headers=self._headers(provider))
            async with httpx.AsyncClient(
                timeout=self.config.scholarly_api_timeout_seconds,
                follow_redirects=True,
            ) as client:
                return await client.get(url, params=params, headers=self._headers(provider))
        except httpx.RequestError as exc:
            raise PaperDiscoveryError(f"{provider} 暂时不可用：{exc}") from exc

    async def search(
        self, query: str, limit: int = 8
    ) -> tuple[QueryKind, list[PaperCandidate], list[str]]:
        query_kind, normalized = detect_query_kind(query)
        if not normalized:
            raise PaperDiscoveryError("请输入论文题名、DOI 或 arXiv ID。", 422)
        warnings: list[str] = []

        if query_kind == "arxiv":
            try:
                candidate = await self._arxiv_by_id(normalized)
                return query_kind, [candidate] if candidate else [], warnings
            except PaperDiscoveryError as exc:
                warnings.append(str(exc))
                candidate = await self._semantic_paper(f"ARXIV:{normalized}")
                return query_kind, [candidate] if candidate else [], warnings

        if query_kind == "doi":
            try:
                candidate = await self._semantic_paper(f"DOI:{normalized}")
                if candidate:
                    return query_kind, [candidate], warnings
            except PaperDiscoveryError as exc:
                warnings.append(str(exc))
            candidate = await self._crossref_by_doi(normalized)
            return query_kind, [candidate] if candidate else [], warnings

        try:
            candidates = await self._semantic_search(normalized, limit)
            if candidates:
                return query_kind, candidates, warnings
        except PaperDiscoveryError as exc:
            warnings.append(str(exc))
        candidates = await self._crossref_search(normalized, limit)
        return query_kind, candidates, warnings

    async def resolve_import(self, source: str, source_id: str) -> PaperCandidate:
        clean_id = source_id.strip()
        if source == "arxiv":
            candidate = await self._arxiv_by_id(clean_id)
        elif source == "semantic_scholar":
            candidate = await self._semantic_paper(clean_id)
        elif source == "crossref":
            candidate = await self._crossref_by_doi(clean_id)
        else:
            raise PaperDiscoveryError("不支持的论文来源。", 422)

        if candidate is None:
            raise PaperDiscoveryError("来源中已找不到这篇论文，请重新检索。", 404)
        if not candidate.importable or not candidate.pdf_url:
            raise PaperDiscoveryError(
                candidate.import_reason or "该来源没有可安全导入的开放 PDF。", 409
            )
        return candidate

    async def download(self, candidate: PaperCandidate) -> StagedUpload:
        if candidate.pdf_url is None or not self._is_safe_pdf_url(candidate.pdf_url):
            raise PaperDiscoveryError("PDF 地址不在服务端可信域名白名单中。", 409)

        async def stage_from(client: httpx.AsyncClient) -> StagedUpload:
            try:
                async with client.stream(
                    "GET",
                    candidate.pdf_url,
                    headers=self._headers(candidate.source),
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    redirects = [str(item.url) for item in response.history]
                    visited_urls = redirects + [str(response.url)]
                    if any(not self._is_safe_pdf_url(url) for url in visited_urls):
                        raise PaperDiscoveryError("PDF 下载发生了不可信的跨域跳转。", 409)
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > self.config.max_upload_bytes:
                        raise PaperDiscoveryError(
                            f"远程 PDF 超过 {self.config.max_upload_mb} MB 限制。", 413
                        )
                    return await storage.stage_pdf_stream(response.aiter_bytes())
            except httpx.HTTPStatusError as exc:
                raise PaperDiscoveryError(
                    f"论文 PDF 下载失败（HTTP {exc.response.status_code}）。", 502
                ) from exc
            except httpx.RequestError as exc:
                raise PaperDiscoveryError(f"论文 PDF 下载失败：{exc}", 502) from exc
            except ValueError as exc:
                raise PaperDiscoveryError("论文 PDF 返回了无效的文件大小。", 502) from exc

        if self.client is not None:
            return await stage_from(self.client)
        async with httpx.AsyncClient(
            timeout=self.config.scholarly_api_timeout_seconds,
            follow_redirects=True,
        ) as client:
            return await stage_from(client)

    async def _semantic_search(self, query: str, limit: int) -> list[PaperCandidate]:
        url = f"{self.config.semantic_scholar_base_url.rstrip('/')}/paper/search"
        response = await self._get(
            url,
            provider="semantic_scholar",
            params={"query": query, "limit": limit, "fields": SEMANTIC_FIELDS},
        )
        if response.status_code == 429:
            raise PaperDiscoveryError("Semantic Scholar 请求频率受限，已尝试备用检索源。")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise PaperDiscoveryError("Semantic Scholar 返回了无效响应。") from exc
        return [self._semantic_candidate(item) for item in payload.get("data", []) if item]

    async def _semantic_paper(self, paper_id: str) -> PaperCandidate | None:
        encoded = quote(paper_id, safe="")
        url = f"{self.config.semantic_scholar_base_url.rstrip('/')}/paper/{encoded}"
        response = await self._get(
            url,
            provider="semantic_scholar",
            params={"fields": SEMANTIC_FIELDS},
        )
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise PaperDiscoveryError("Semantic Scholar 请求频率受限，稍后可重试。")
        try:
            response.raise_for_status()
            return self._semantic_candidate(response.json())
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise PaperDiscoveryError("Semantic Scholar 返回了无效响应。") from exc

    def _semantic_candidate(self, item: dict) -> PaperCandidate:
        external_ids = item.get("externalIds") or {}
        arxiv_raw = str(external_ids.get("ArXiv") or "")
        arxiv = _extract_arxiv_id(arxiv_raw) if arxiv_raw else None
        doi = _extract_doi(str(external_ids.get("DOI") or ""))
        open_pdf = item.get("openAccessPdf") or {}
        candidate_pdf = str(open_pdf.get("url") or "") or None
        if arxiv:
            base, version = arxiv
            identifier = f"{base}v{version}" if version else base
            candidate_pdf = f"https://arxiv.org/pdf/{identifier}.pdf"
        safe_pdf = candidate_pdf if candidate_pdf and self._is_safe_pdf_url(candidate_pdf) else None
        return PaperCandidate(
            source="semantic_scholar",
            source_id=str(item.get("paperId") or doi or arxiv_raw),
            title=_clean_text(item.get("title")) or "未命名论文",
            authors=[
                _clean_text(author.get("name"))
                for author in item.get("authors") or []
                if _clean_text(author.get("name"))
            ],
            abstract=_clean_text(item.get("abstract")) or None,
            year=item.get("year"),
            published_at=item.get("publicationDate"),
            venue=_clean_text(item.get("venue")) or None,
            doi=doi,
            arxiv_id=arxiv[0] if arxiv else None,
            arxiv_version=arxiv[1] if arxiv else None,
            citation_count=item.get("citationCount"),
            influential_citation_count=item.get("influentialCitationCount"),
            landing_url=item.get("url"),
            pdf_url=safe_pdf,
            license=open_pdf.get("license") or open_pdf.get("status"),
            importable=bool(safe_pdf),
            import_reason=None if safe_pdf else "未发现位于可信来源的开放 PDF。",
        )

    async def _arxiv_by_id(self, arxiv_id: str) -> PaperCandidate | None:
        normalized = _extract_arxiv_id(arxiv_id)
        if normalized is None:
            raise PaperDiscoveryError("arXiv ID 格式无效。", 422)
        base, version = normalized
        requested = f"{base}v{version}" if version else base
        url = f"{self.config.arxiv_api_base_url.rstrip('/')}/query"
        response = await self._get(
            url,
            provider="arxiv",
            params={"id_list": requested, "max_results": 1},
        )
        try:
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPStatusError, ElementTree.ParseError) as exc:
            raise PaperDiscoveryError("arXiv 返回了无效响应。") from exc
        entry = root.find("{http://www.w3.org/2005/Atom}entry")
        return self._arxiv_candidate(entry) if entry is not None else None

    def _arxiv_candidate(self, entry: ElementTree.Element) -> PaperCandidate:
        atom = "{http://www.w3.org/2005/Atom}"
        arxiv_ns = "{http://arxiv.org/schemas/atom}"
        entry_url = _clean_text(entry.findtext(f"{atom}id"))
        identity = _extract_arxiv_id(entry_url)
        if identity is None:
            raise PaperDiscoveryError("arXiv 条目缺少有效标识。")
        base, version = identity
        identifier = f"{base}v{version}" if version else base
        pdf_url = f"https://arxiv.org/pdf/{identifier}.pdf"
        doi = _extract_doi(_clean_text(entry.findtext(f"{arxiv_ns}doi")))
        published = _clean_text(entry.findtext(f"{atom}published")) or None
        authors = [
            _clean_text(author.findtext(f"{atom}name"))
            for author in entry.findall(f"{atom}author")
        ]
        categories = [item.attrib.get("term", "") for item in entry.findall(f"{atom}category")]
        license_link = next(
            (
                item.attrib.get("href")
                for item in entry.findall(f"{atom}link")
                if item.attrib.get("rel") == "license"
            ),
            None,
        )
        return PaperCandidate(
            source="arxiv",
            source_id=identifier,
            title=_clean_text(entry.findtext(f"{atom}title")) or "未命名论文",
            authors=[author for author in authors if author],
            abstract=_clean_text(entry.findtext(f"{atom}summary")) or None,
            year=int(published[:4]) if published and published[:4].isdigit() else None,
            published_at=published,
            venue=", ".join(filter(None, categories)) or None,
            doi=doi,
            arxiv_id=base,
            arxiv_version=version,
            landing_url=entry_url,
            pdf_url=pdf_url,
            license=license_link,
            importable=True,
        )

    async def _crossref_search(self, query: str, limit: int) -> list[PaperCandidate]:
        url = f"{self.config.crossref_base_url.rstrip('/')}/works"
        response = await self._get(
            url,
            provider="crossref",
            params={"query.bibliographic": query, "rows": limit},
        )
        return self._crossref_items(response)

    async def _crossref_by_doi(self, doi: str) -> PaperCandidate | None:
        normalized = _extract_doi(doi)
        if normalized is None:
            raise PaperDiscoveryError("DOI 格式无效。", 422)
        url = f"{self.config.crossref_base_url.rstrip('/')}/works/{quote(normalized, safe='')}"
        response = await self._get(url, provider="crossref")
        if response.status_code == 404:
            return None
        items = self._crossref_items(response, detail=True)
        return items[0] if items else None

    def _crossref_items(
        self, response: httpx.Response, detail: bool = False
    ) -> list[PaperCandidate]:
        try:
            response.raise_for_status()
            message = response.json().get("message", {})
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise PaperDiscoveryError("Crossref 返回了无效响应。") from exc
        raw_items = [message] if detail else message.get("items", [])
        return [self._crossref_candidate(item) for item in raw_items if item]

    def _crossref_candidate(self, item: dict) -> PaperCandidate:
        titles = item.get("title") or []
        doi = _extract_doi(str(item.get("DOI") or ""))
        date_parts = ((item.get("published") or {}).get("date-parts") or [[]])[0]
        published_at = "-".join(str(value).zfill(2) for value in date_parts) or None
        authors = []
        for author in item.get("author") or []:
            name_parts = (
                _clean_text(author.get("given")),
                _clean_text(author.get("family")),
            )
            name = " ".join(
                value for value in name_parts if value
            )
            if name:
                authors.append(name)
        return PaperCandidate(
            source="crossref",
            source_id=doi or str(item.get("URL") or ""),
            title=_clean_text(titles[0]) if titles else "未命名论文",
            authors=authors,
            abstract=_clean_text(item.get("abstract")) or None,
            year=date_parts[0] if date_parts and isinstance(date_parts[0], int) else None,
            published_at=published_at,
            venue=_clean_text((item.get("container-title") or [""])[0]) or None,
            doi=doi,
            citation_count=item.get("is-referenced-by-count"),
            landing_url=item.get("URL"),
            importable=False,
            import_reason="Crossref 当前仅用于元数据兜底，未提供可验证的开放 PDF。",
        )

    def _is_safe_pdf_url(self, value: str) -> bool:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        return parsed.hostname.lower() in self.allowed_pdf_hosts


def candidate_filename(candidate: PaperCandidate) -> str:
    if candidate.arxiv_id:
        version = f"v{candidate.arxiv_version}" if candidate.arxiv_version else ""
        return f"{candidate.arxiv_id}{version}.pdf"
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", candidate.title).strip(" .-")
    return f"{(clean or Path(candidate.source_id).stem)[:180]}.pdf"


paper_discovery = PaperDiscoveryService()
