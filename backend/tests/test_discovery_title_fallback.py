"""Title fallbacks are real provider choices, not retries or invented metadata."""

import httpx
import pytest

from app.core.config import Settings
from app.services.paper_discovery import PaperDiscoveryService

FEED = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>https://arxiv.org/abs/2010.11929v2</id>
<title>An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale</title>
<author><name>Alexey Dosovitskiy</name></author>
</entry></feed>"""
TITLE = "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale"


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_status", [200, 429, 503])
async def test_missing_primary_title_uses_arxiv_not_crossref(primary_status):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(primary_status, json={"data": []})
        assert request.url.host == "export.arxiv.org"
        assert request.url.params["search_query"] == f'ti:"{TITLE}"'
        assert request.url.params["max_results"] == "3"
        return httpx.Response(200, content=FEED)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kind, items, warnings = await PaperDiscoveryService(Settings(), client).search(TITLE, 3)
    assert calls == ["api.semanticscholar.org", "export.arxiv.org"]
    assert kind == "title"
    assert items[0].arxiv_id == "2010.11929"
    assert items[0].arxiv_version == 2
    assert items[0].importable
    assert bool(warnings) == (primary_status != 200)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content", "warning"),
    [
        (200, b'<feed xmlns="http://www.w3.org/2005/Atom"/>', False),
        (429, b"rate limited", True),
        (503, b"unavailable", True),
        (200, b"invalid xml", True),
        (200, b"<html/>", True),
    ],
)
async def test_unavailable_arxiv_title_preserves_crossref_fallback(status, content, warning):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json={"data": []})
        if request.url.host == "export.arxiv.org":
            return httpx.Response(status, content=content)
        assert request.url.host == "api.crossref.org"
        return httpx.Response(
            200, json={"message": {"items": [{"DOI": "10.1234/example", "title": [TITLE]}]}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, items, warnings = await PaperDiscoveryService(Settings(), client).search(TITLE)
    assert calls == ["api.semanticscholar.org", "export.arxiv.org", "api.crossref.org"]
    assert items[0].source == "crossref"
    assert not items[0].importable
    assert bool(warnings) == warning
    if status == 429:
        assert "HTTP 429" in warnings[0]


@pytest.mark.asyncio
async def test_title_phrase_cannot_inject_arxiv_field_operators():
    queries = []

    def handler(request):
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json={"data": []})
        if request.url.host == "export.arxiv.org":
            queries.append(request.url.params["search_query"])
            return httpx.Response(200, content=FEED)
        raise AssertionError("Unexpected fallback")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await PaperDiscoveryService(Settings(), client).search('paper" OR all:"physics')
    assert queries == ['ti:"paper OR all:physics"']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError]
)
async def test_empty_network_error_keeps_type_and_never_exposes_endpoint_credentials(error_type):
    from app.services.paper_discovery import PaperDiscoveryError

    calls = []

    def handler(request):
        calls.append(request)
        raise error_type("", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PaperDiscoveryService(Settings(scholarly_api_timeout_seconds=20), client)
        with pytest.raises(PaperDiscoveryError) as caught:
            await service._get(
                "https://user:credential@example.test/api?key=secret", provider="arxiv"
            )
    assert len(calls) == 1
    message = str(caught.value)
    assert error_type.__name__ in message
    assert "未自动重试" in message
    assert "credential" not in message and "secret" not in message
    if issubclass(error_type, httpx.TimeoutException):
        assert "20s" in message
