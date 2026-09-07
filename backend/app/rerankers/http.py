import math
from typing import Any

import httpx

from app.schemas.agent import EvidenceAnchor


class RerankerServiceError(RuntimeError):
    pass


class HttpReranker:
    """Cross-encoder adapter for Cohere-compatible ``POST /rerank`` APIs."""

    name = "cohere-compatible"
    enabled = True

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        candidate_k: int = 12,
        retrieval_weight: float = 0.25,
        client: httpx.Client | None = None,
    ) -> None:
        if not model.strip():
            raise RerankerServiceError("启用 Reranker 时必须配置模型名称。")
        if not base_url.strip():
            raise RerankerServiceError("启用 Reranker 时必须配置 BASE_URL。")
        if not 0.0 <= retrieval_weight <= 1.0:
            raise RerankerServiceError("RERANKER_RETRIEVAL_WEIGHT 必须在 0 到 1 之间。")
        if candidate_k < 1:
            raise RerankerServiceError("RERANKER_CANDIDATE_K 必须大于 0。")
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.candidate_k = candidate_k
        self.retrieval_weight = retrieval_weight
        self._client = client

    def rerank(
        self, question: str, evidence: list[EvidenceAnchor], top_k: int
    ) -> list[EvidenceAnchor]:
        if not evidence or top_k <= 0:
            return []
        payload = {
            "model": self.model,
            "query": question,
            "documents": [item.quote for item in evidence],
            "top_n": len(evidence),
            "return_documents": False,
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
            raise RerankerServiceError(f"Reranker 服务调用失败: {exc}") from exc
        scores = self._parse_scores(body, evidence_count=len(evidence))
        return self._merge_scores(evidence, scores, top_k)

    @staticmethod
    def _parse_scores(body: Any, *, evidence_count: int) -> dict[int, float]:
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise RerankerServiceError("Reranker 服务响应格式无效。")
        scores: dict[int, float] = {}
        try:
            for result in body["results"]:
                index = int(result["index"])
                value = result.get("relevance_score", result.get("score"))
                score = float(value)
                if index < 0 or index >= evidence_count or index in scores:
                    raise ValueError
                if not math.isfinite(score):
                    raise ValueError
                scores[index] = score
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RerankerServiceError("Reranker 服务响应中的索引或分数无效。") from exc
        if not scores:
            raise RerankerServiceError("Reranker 服务未返回有效分数。")
        return scores

    def _merge_scores(
        self,
        evidence: list[EvidenceAnchor],
        reranker_scores: dict[int, float],
        top_k: int,
    ) -> list[EvidenceAnchor]:
        values = list(reranker_scores.values())
        low, high = min(values), max(values)
        normalized = {
            index: (score - low) / (high - low) if high > low else 0.5
            for index, score in reranker_scores.items()
        }
        candidates = []
        for index, item in enumerate(evidence):
            rerank_score = normalized.get(index, 0.0)
            combined = (
                (1.0 - self.retrieval_weight) * rerank_score
                + self.retrieval_weight * item.score
            )
            candidates.append((combined, -index, item))
        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)

        results = []
        for rank, (score, _, item) in enumerate(candidates[:top_k], start=1):
            copy = item.model_copy(deep=True)
            copy.evidence_id = f"E{rank}"
            copy.retrieval_mode = "reranked"
            copy.score = round(min(1.0, max(0.0, score)), 4)
            results.append(copy)
        return results
