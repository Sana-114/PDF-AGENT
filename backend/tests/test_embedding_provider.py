import httpx
import pytest

from app.core.config import Settings
from app.embeddings.factory import get_embedding_provider
from app.embeddings.openai_compatible import (
    EmbeddingServiceError,
    OpenAICompatibleEmbeddingProvider,
)


def _provider(handler, dimensions: int = 3) -> OpenAICompatibleEmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        model="BAAI/bge-m3",
        dimensions=dimensions,
        base_url="http://embedding.test/v1/",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_openai_compatible_embedding_validates_and_orders_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://embedding.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer secret"
        assert b'"model":"BAAI/bge-m3"' in request.content
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                ]
            },
        )

    provider = _provider(handler)

    assert provider.embed_dense(["first", "second"]) == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert provider.index_namespace.startswith("semantic_")
    assert provider.embed_sparse("Adam optimizer").indices


def test_openai_compatible_embedding_rejects_wrong_dimensions() -> None:
    provider = _provider(
        lambda _: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )
    )

    with pytest.raises(EmbeddingServiceError, match="期望 3"):
        provider.embed_dense(["wrong dimensions"])


def test_embedding_factory_builds_remote_provider_without_network_call() -> None:
    provider = get_embedding_provider(
        Settings(
            embedding_provider="openai-compatible",
            embedding_model="BAAI/bge-m3",
            embedding_dimensions=1024,
            embedding_base_url="http://embedding.local/v1",
        )
    )

    assert provider.name == "openai-compatible"
    assert provider.model == "BAAI/bge-m3"
    assert provider.dimensions == 1024
