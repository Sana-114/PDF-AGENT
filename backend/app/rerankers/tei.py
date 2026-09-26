import math
from contextlib import nullcontext
from typing import Any

import httpx

from app.rerankers.http import HttpReranker, RerankerServiceError
from app.schemas.agent import EvidenceAnchor


class TeiReranker(HttpReranker):
    """Adapter for Hugging Face Text Embeddings Inference ``POST /rerank``."""

    name = "tei"
    # TEI's configured max-client-batch-size is 32 in the local BGE profile.
    # Leave headroom for deployments with a smaller limit and long PDF chunks.
    max_batch_size = 16

    def rerank(
        self, question: str, evidence: list[EvidenceAnchor], top_k: int
    ) -> list[EvidenceAnchor]:
        if not evidence or top_k <= 0:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        scores: dict[int, float] = {}
        try:
            client_context = (
                nullcontext(self._client)
                if self._client is not None
                else httpx.Client(timeout=self.timeout_seconds)
            )
            with client_context as client:
                for start in range(0, len(evidence), self.max_batch_size):
                    batch = evidence[start : start + self.max_batch_size]
                    response = client.post(
                        f"{self.base_url}/rerank",
                        json={
                            "query": question,
                            "texts": [item.quote for item in batch],
                            "truncate": True,
                            "raw_scores": False,
                            "return_text": False,
                        },
                        headers=headers,
                    )
                    response.raise_for_status()
                    batch_scores = self._parse_tei_scores(
                        response.json(), evidence_count=len(batch)
                    )
                    scores.update({start + index: score for index, score in batch_scores.items()})
        except (httpx.HTTPError, ValueError) as exc:
            raise RerankerServiceError(f"TEI Reranker 服务调用失败: {exc}") from exc
        return self._merge_scores(evidence, scores, top_k)

    @staticmethod
    def _parse_tei_scores(body: Any, *, evidence_count: int) -> dict[int, float]:
        results: Any = body
        if isinstance(body, dict):
            results = body.get("ranks", body.get("results"))
        if not isinstance(results, list):
            raise RerankerServiceError("TEI Reranker 服务响应格式无效。")

        scores: dict[int, float] = {}
        try:
            for result in results:
                index = int(result["index"])
                value = result.get("score", result.get("relevance_score"))
                score = float(value)
                if index < 0 or index >= evidence_count or index in scores:
                    raise ValueError
                if not math.isfinite(score):
                    raise ValueError
                scores[index] = score
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RerankerServiceError("TEI Reranker 服务响应中的索引或分数无效。") from exc
        if not scores:
            raise RerankerServiceError("TEI Reranker 服务未返回有效分数。")
        return scores
