import json

import httpx
import pytest

from app.core.config import Settings
from app.rerankers.factory import get_reranker
from app.rerankers.http import HttpReranker, RerankerServiceError
from app.rerankers.tei import TeiReranker
from app.schemas.agent import EvidenceAnchor
from app.services.retrieval import HybridRetriever


def _evidence(chunk_id: str, quote: str, score: float) -> EvidenceAnchor:
    return EvidenceAnchor(
        evidence_id="candidate",
        chunk_id=chunk_id,
        document_id="doc-1",
        page_number=1,
        quote=quote,
        score=score,
        retrieval_mode="hybrid",
    )


def test_http_cross_encoder_reranks_candidates_and_preserves_anchors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://reranker.test/v1/rerank"
        assert request.headers["authorization"] == "Bearer secret"
        assert b'"top_n":2' in request.content
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.95},
                    {"index": 0, "relevance_score": 0.1},
                ]
            },
        )

    reranker = HttpReranker(
        model="BAAI/bge-reranker-v2-m3",
        base_url="http://reranker.test/v1/",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    candidates = [
        _evidence("first", "generic optimizer text", 0.9),
        _evidence("target", "beta_1 is 0.9 and beta_2 is 0.98", 0.6),
    ]

    results = reranker.rerank("Which beta values were used?", candidates, top_k=2)

    assert results[0].chunk_id == "target"
    assert results[0].quote == candidates[1].quote
    assert results[0].retrieval_mode == "reranked"
    assert [item.evidence_id for item in results] == ["E1", "E2"]


def test_http_cross_encoder_rejects_invalid_result_indices() -> None:
    reranker = HttpReranker(
        model="reranker",
        base_url="http://reranker.test/v1",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"results": [{"index": 4, "relevance_score": 1.0}]},
                )
            )
        ),
    )

    with pytest.raises(RerankerServiceError, match="索引或分数无效"):
        reranker.rerank("question", [_evidence("one", "text", 0.5)], top_k=1)


def test_tei_cross_encoder_uses_native_request_and_response_format() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://reranker.test/rerank"
        payload = json.loads(request.content)
        assert payload == {
            "query": "Which beta values were used?",
            "texts": ["generic optimizer text", "beta_1 is 0.9 and beta_2 is 0.98"],
            "truncate": True,
            "raw_scores": False,
            "return_text": False,
        }
        return httpx.Response(
            200,
            json=[{"index": 1, "score": 0.95}, {"index": 0, "score": 0.1}],
        )

    reranker = TeiReranker(
        model="BAAI/bge-reranker-v2-m3",
        base_url="http://reranker.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    candidates = [
        _evidence("first", "generic optimizer text", 0.9),
        _evidence("target", "beta_1 is 0.9 and beta_2 is 0.98", 0.6),
    ]

    results = reranker.rerank("Which beta values were used?", candidates, top_k=2)

    assert results[0].chunk_id == "target"
    assert results[0].retrieval_mode == "reranked"
    assert [item.evidence_id for item in results] == ["E1", "E2"]


def test_tei_cross_encoder_accepts_wrapped_rank_response() -> None:
    reranker = TeiReranker(
        model="reranker",
        base_url="http://reranker.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"ranks": [{"index": 0, "score": 0.75}]},
                )
            )
        ),
    )

    results = reranker.rerank(
        "question", [_evidence("one", "answer text", 0.5)], top_k=1
    )

    assert results[0].chunk_id == "one"


def test_hybrid_retriever_preserves_rank_when_reranker_fails() -> None:
    class FailingReranker:
        name = "failing"
        model = "test"
        enabled = True
        candidate_k = 12

        def rerank(self, question, evidence, top_k):
            del question, evidence, top_k
            raise ConnectionError("offline")

    retriever = object.__new__(HybridRetriever)
    retriever.reranker = FailingReranker()
    candidates = [
        _evidence("first", "first evidence", 0.9),
        _evidence("second", "second evidence", 0.8),
    ]

    results = retriever._rerank("question", candidates, 1, "hybrid")

    assert results[0].chunk_id == "first"
    assert results[0].retrieval_mode == "hybrid"
    assert results[0].evidence_id == "E1"


def test_reranker_is_disabled_by_default_and_configurable() -> None:
    assert get_reranker(Settings()).enabled is False

    reranker = get_reranker(
        Settings(
            reranker_provider="cohere-compatible",
            reranker_model="BAAI/bge-reranker-v2-m3",
            reranker_base_url="http://reranker.local/v1",
            reranker_candidate_k=16,
        )
    )

    assert reranker.enabled is True
    assert reranker.candidate_k == 16

    tei_reranker = get_reranker(
        Settings(
            reranker_provider="tei",
            reranker_model="BAAI/bge-reranker-v2-m3",
            reranker_base_url="http://bge-reranker",
        )
    )
    assert isinstance(tei_reranker, TeiReranker)
    assert tei_reranker.name == "tei"
