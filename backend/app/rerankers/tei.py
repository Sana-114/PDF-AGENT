import math
from typing import Any

import httpx

from app.rerankers.http import HttpReranker, RerankerServiceError
from app.schemas.agent import EvidenceAnchor


class TeiReranker(HttpReranker):
    """Adapter for Hugging Face Text Embeddings Inference ``POST /rerank``."""

    name = "tei"

    def rerank(
        self, question: str, evidence: list[EvidenceAnchor], top_k: int
    ) -> list[EvidenceAnchor]:
        if not evidence or top_k <= 0:
            return []
        payload = {
            "query": question,
            "texts": [item.quote for item in evidence],
            "truncate": True,
            "raw_scores": False,
            "return_text": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            if self._client is not None:
                response = self._client.post(
                    f"{self.base_url}/rerank", json=payload, headers=headers
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}/rerank", json=payload, headers=headers
                    )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RerankerServiceError(f"TEI Reranker 服务调用失败: {exc}") from exc
        scores = self._parse_tei_scores(body, evidence_count=len(evidence))
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
            raise RerankerServiceError(
                "TEI Reranker 服务响应中的索引或分数无效。"
            ) from exc
        if not scores:
            raise RerankerServiceError("TEI Reranker 服务未返回有效分数。")
        return scores
