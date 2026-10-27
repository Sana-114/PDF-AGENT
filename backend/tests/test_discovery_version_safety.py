import httpx
import pytest

from app.core.config import Settings
from app.services.paper_discovery import PaperDiscoveryError, PaperDiscoveryService


@pytest.mark.asyncio
async def test_arxiv_fallback_uses_work_identity_and_blocks_unverified_revision():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "export.arxiv.org":
            return httpx.Response(429, text="Too many requests")
        assert request.url.path.endswith("/paper/ARXIV:2106.09685")
        return httpx.Response(
            200,
            json={"paperId": "lora-id", "title": "LoRA", "externalIds": {"ArXiv": "2106.09685"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PaperDiscoveryService(Settings(), client)
        _, papers, warnings = await service.search("arxiv:2106.09685v2")
    assert len(requests) == 2
    assert papers[0].arxiv_version is None
    assert papers[0].importable is False
    assert any("无法核验" in w for w in warnings)
    assert any("HTTP 429" in w for w in warnings)


@pytest.mark.asyncio
async def test_arxiv_tracking_exposes_rate_limit_without_retrying():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PaperDiscoveryService(Settings(), client)
        with pytest.raises(PaperDiscoveryError, match="HTTP 429") as error:
            await service.search_arxiv(query="low rank")
    assert error.value.status_code == 429
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_import_resolution_rejects_arxiv_revision_mismatch():
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2106.09685v3</id><title>LoRA</title></entry></feed>'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=feed))
    ) as client:
        with pytest.raises(PaperDiscoveryError, match="版本与请求不一致"):
            await PaperDiscoveryService(Settings(), client).resolve_import("arxiv", "2106.09685v2")
