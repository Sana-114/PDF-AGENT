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
    # Two BGE services share an 8 GiB GPU in the standard local profile.
    # Long PDF blocks must not be batched like sixteen short sentences. Bound
    # total UTF-8 input bytes (not an exact tokenizer budget), without changing
    # any quote/anchor or silently dropping a candidate. An oversize single
    # passage is sent alone with the existing explicit server truncation flag.
    max_batch_bytes = 12000

    @staticmethod
    def _scoring_passage(item: EvidenceAnchor) -> str:
        """Keep real local headings for passages referring to 'this objective'.

        Only the scorer sees this bounded context; the original quote/anchor is
        never rewritten and headings alone cannot support a published claim.
        """
        context = []
        for label, value in (("Document", item.document_title), ("Section", item.section)):
            if value and value.strip():
                context.append(f"{label}: {' '.join(value.split())[:160]}")
        return "\n".join([*context, item.quote]) if context else item.quote

    def _batches(self, question: str, evidence: list[EvidenceAnchor]):
        batch: list[EvidenceAnchor] = []
        start, size = 0, 0
        for index, item in enumerate(evidence):
            item_size = len((question + self._scoring_passage(item)).encode("utf-8"))
            if batch and (len(batch) >= self.max_batch_size
                          or size + item_size > self.max_batch_bytes):
                yield start, batch
                start, batch, size = index, [], 0
            batch.append(item)
            size += item_size
        if batch:
            yield start, batch

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
                for start, batch in self._batches(question, evidence):
                    response = client.post(
                        f"{self.base_url}/rerank",
                        json={
                            "query": question,
                            "texts": [self._scoring_passage(item) for item in batch],
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
