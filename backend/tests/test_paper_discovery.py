import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import discovery as discovery_routes
from app.api.routes.discovery import get_paper_discovery_service
from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.models.paper_source import PaperSource
from app.schemas.discovery import PaperCandidate, PaperImportRequest
from app.services.paper_discovery import (
    PaperDiscoveryError,
    PaperDiscoveryService,
    detect_query_kind,
)
from app.services.storage import StagedUpload


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
        translation_dir=tmp_path / "translations",
        semantic_scholar_api_key="",
    )


def test_detect_query_kind_normalizes_identifiers() -> None:
    assert detect_query_kind("arXiv:1706.03762v7") == ("arxiv", "1706.03762v7")
    assert detect_query_kind("https://doi.org/10.48550/arXiv.1706.03762") == (
        "doi",
        "10.48550/arxiv.1706.03762",
    )


def test_discovery_endpoint_exposes_normalized_results() -> None:
    class FakeDiscovery:
        async def search(self, query: str, limit: int):
            assert query == "Attention Is All You Need"
            assert limit == 8
            return (
                "title",
                [
                    PaperCandidate(
                        source="arxiv",
                        source_id="1706.03762v7",
                        title=query,
                        importable=True,
                        pdf_url="https://arxiv.org/pdf/1706.03762v7.pdf",
                    )
                ],
                [],
            )

    app.dependency_overrides[get_paper_discovery_service] = lambda: FakeDiscovery()
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/discovery/papers", params={"q": "Attention Is All You Need"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["query_kind"] == "title"
    assert response.json()["items"][0]["importable"] is True
    assert detect_query_kind("  Attention   Is All You Need ") == (
        "title",
        "Attention Is All You Need",
    )
    assert detect_query_kind("Benchmark results for 2020.1234 samples") == (
        "title",
        "Benchmark results for 2020.1234 samples",
    )


@pytest.mark.asyncio
async def test_title_search_normalizes_semantic_scholar_result(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/paper/search")
        assert request.url.params["query"] == "Attention Is All You Need"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paperId": "s2-paper-id",
                        "title": "Attention Is All You Need",
                        "abstract": "A sequence model.",
                        "authors": [{"name": "Ashish Vaswani"}],
                        "year": 2017,
                        "publicationDate": "2017-06-12",
                        "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222"},
                        "citationCount": 120000,
                        "influentialCitationCount": 9000,
                        "openAccessPdf": {"url": "https://example.org/paper.pdf"},
                        "url": "https://www.semanticscholar.org/paper/s2-paper-id",
                        "venue": "NeurIPS",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PaperDiscoveryService(_settings(tmp_path), client)
        kind, items, warnings = await service.search("Attention Is All You Need")

    assert kind == "title"
    assert warnings == []
    assert items[0].source_id == "s2-paper-id"
    assert items[0].authors == ["Ashish Vaswani"]
    assert items[0].arxiv_id == "1706.03762"
    # A canonical arXiv PDF is derived rather than trusting the arbitrary upstream URL.
    assert items[0].pdf_url == "https://arxiv.org/pdf/1706.03762.pdf"
    assert items[0].importable is True


@pytest.mark.asyncio
async def test_arxiv_identifier_uses_exact_id_list_and_keeps_version(tmp_path) -> None:
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>https://arxiv.org/abs/1706.03762v7</id>
        <published>2017-06-12T17:57:34Z</published>
        <title>Attention Is All You Need</title>
        <summary>Transformer architecture.</summary>
        <author><name>Ashish Vaswani</name></author>
        <category term="cs.CL" />
        <arxiv:doi>10.5555/3295222.3295349</arxiv:doi>
      </entry>
    </feed>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id_list"] == "1706.03762v7"
        return httpx.Response(200, content=feed)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PaperDiscoveryService(_settings(tmp_path), client)
        kind, items, _ = await service.search("arXiv:1706.03762v7")

    assert kind == "arxiv"
    assert items[0].arxiv_id == "1706.03762"
    assert items[0].arxiv_version == 7
    assert items[0].source_id == "1706.03762v7"
    assert items[0].pdf_url.endswith("1706.03762v7.pdf")


@pytest.mark.asyncio
async def test_download_rejects_untrusted_pdf_host(tmp_path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        service = PaperDiscoveryService(_settings(tmp_path), client)
        candidate = PaperCandidate(
            source="semantic_scholar",
            source_id="paper-id",
            title="Unsafe",
            pdf_url="https://127.0.0.1/private.pdf",
            importable=True,
        )
        with pytest.raises(PaperDiscoveryError, match="白名单"):
            await service.download(candidate)


@pytest.mark.asyncio
async def test_download_streams_and_validates_pdf(tmp_path, monkeypatch) -> None:
    pdf = b"%PDF-1.7\nsmall test document"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "arxiv.org"
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    from app.services import storage as storage_module

    monkeypatch.setattr(storage_module.settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(storage_module.settings, "data_dir", tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PaperDiscoveryService(_settings(tmp_path), client)
        candidate = PaperCandidate(
            source="arxiv",
            source_id="1706.03762v7",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            arxiv_version=7,
            pdf_url="https://arxiv.org/pdf/1706.03762v7.pdf",
            importable=True,
        )
        staged = await service.download(candidate)

    assert staged.size_bytes == len(pdf)
    assert staged.temporary_path.read_bytes() == pdf
    staged.discard()


@pytest.mark.asyncio
async def test_import_persists_source_and_reuses_ingestion_pipeline(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    temporary_path = upload_dir / ".remote.part"
    temporary_path.write_bytes(b"%PDF-1.7\nremote")
    candidate = PaperCandidate(
        source="arxiv",
        source_id="1706.03762v7",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        arxiv_id="1706.03762",
        arxiv_version=7,
        landing_url="https://arxiv.org/abs/1706.03762v7",
        pdf_url="https://arxiv.org/pdf/1706.03762v7.pdf",
        importable=True,
    )

    class FakeDiscovery:
        async def resolve_import(self, source: str, source_id: str) -> PaperCandidate:
            assert (source, source_id) == ("arxiv", "1706.03762v7")
            return candidate

        async def download(self, _: PaperCandidate) -> StagedUpload:
            return StagedUpload(
                temporary_path=temporary_path,
                sha256="a" * 64,
                size_bytes=15,
                storage_key="remote.pdf",
            )

    from app.services import storage as storage_module

    monkeypatch.setattr(storage_module.settings, "upload_dir", upload_dir)
    dispatched: list[str] = []
    monkeypatch.setattr(
        discovery_routes,
        "dispatch_document_parse",
        lambda _db, document: dispatched.append(document.id),
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = await discovery_routes.import_paper(
            PaperImportRequest(source="arxiv", source_id="1706.03762v7"),
            session,
            FakeDiscovery(),
        )
        provenance = session.get(PaperSource, result.document.id)

    assert result.exact_duplicate is False
    assert result.document.title == "Attention Is All You Need"
    assert dispatched == [result.document.id]
    assert provenance is not None
    assert provenance.source_id == "1706.03762v7"
    assert (upload_dir / "remote.pdf").exists()
