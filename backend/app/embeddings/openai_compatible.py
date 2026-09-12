import hashlib
import math
from typing import Any

import httpx

from app.embeddings.base import SparseEmbedding
from app.embeddings.hashing import HashEmbeddingProvider


class EmbeddingServiceError(RuntimeError):
    pass


class OpenAICompatibleEmbeddingProvider:
    """Dense embeddings from an OpenAI-compatible HTTP endpoint.

    The remote model supplies learned dense vectors. The existing deterministic
    hash sparse representation remains available for Qdrant's second recall path.
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not model.strip():
            raise EmbeddingServiceError("远程 Embedding Provider 必须配置模型名称。")
        if dimensions < 1:
            raise EmbeddingServiceError("EMBEDDING_DIMENSIONS 必须大于 0。")
        if not base_url.strip():
            raise EmbeddingServiceError("远程 Embedding Provider 必须配置 BASE_URL。")
        self.model = model.strip()
        self.dimensions = dimensions
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client
        signature = hashlib.sha256(self.model.encode("utf-8")).hexdigest()[:10]
        self.index_namespace = f"semantic_{signature}"
        self._sparse_provider = HashEmbeddingProvider(dimensions=max(dimensions, 32))

    def embed_dense(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts, "encoding_format": "float"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            if self._client is not None:
                response = self._client.post(
                    f"{self.base_url}/embeddings", json=payload, headers=headers
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}/embeddings", json=payload, headers=headers
                    )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingServiceError(f"Embedding 服务调用失败: {exc}") from exc
        return self._parse_vectors(body, expected_count=len(texts))

    def embed_sparse(self, text: str) -> SparseEmbedding:
        return self._sparse_provider.embed_sparse(text)

    def _parse_vectors(self, body: Any, *, expected_count: int) -> list[list[float]]:
        if not isinstance(body, dict):
            raise EmbeddingServiceError("Embedding 服务响应格式无效。")
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise EmbeddingServiceError("Embedding 服务返回的向量数量与输入不一致。")
        try:
            ordered = sorted(data, key=lambda item: int(item["index"]))
            indices = [int(item["index"]) for item in ordered]
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingServiceError("Embedding 服务响应格式无效。") from exc
        if indices != list(range(expected_count)):
            raise EmbeddingServiceError("Embedding 服务返回的向量索引无效。")
        if any(len(vector) != self.dimensions for vector in vectors):
            raise EmbeddingServiceError(
                f"Embedding 服务返回维度与配置不一致，期望 {self.dimensions}。"
            )
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise EmbeddingServiceError("Embedding 服务返回了非有限数值。")
        return vectors
