import json

import httpx

from app.rerankers.tei import TeiReranker
from app.schemas.agent import EvidenceAnchor


def anchor(n, quote, title=None, section=None):
    return EvidenceAnchor(evidence_id=f"E{n}", chunk_id=str(n), document_id="doc",
                          document_title=title, section=section, page_number=7,
                          quote=quote, score=0.8, block_ids=[f"p7-b{n}"], bbox=[1, 2, 30, 40])


def test_scoring_context_is_bounded_real_metadata_not_a_rewritten_source_quote():
    source = anchor(1, "The motivation for this objective is stability.",
                    "An Example Method\n", "3\n Objective")
    assert TeiReranker._scoring_passage(source) == (
        "Document: An Example Method\nSection: 3 Objective\n" + source.quote)
    source.document_title = "名" * 500
    source.section = "节" * 500
    text = TeiReranker._scoring_passage(source)
    assert text.count("名") == 160 and text.count("节") == 160
    assert text.endswith(source.quote)
    assert TeiReranker._scoring_passage(anchor(2, "Plain source text")) == "Plain source text"


def test_tei_request_has_source_context_but_returned_anchor_remains_exact():
    source = anchor(1, "The motivation is limited drift.", "Method Alpha", "Objective")
    before = source.model_dump()

    def handler(request):
        payload = json.loads(request.content)
        assert payload["query"] == "Why does this method work?"
        assert payload["texts"] == [
            "Document: Method Alpha\nSection: Objective\nThe motivation is limited drift."]
        return httpx.Response(200, json=[{"index": 0, "score": 0.9}])

    reranker = TeiReranker(model="synthetic", base_url="http://reranker.test",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = reranker.rerank("Why does this method work?", [source], 1)[0]
    assert source.model_dump() == before
    assert result.quote == source.quote and result.block_ids == source.block_ids
    assert result.bbox == source.bbox and result.page_number == source.page_number
    assert result.document_title == source.document_title and result.section == source.section
    assert result.retrieval_mode == "reranked"


def test_byte_budget_includes_context_and_question_without_dropping_passages():
    reranker = TeiReranker(model="synthetic", base_url="http://reranker.test")
    reranker.max_batch_bytes = 1800
    sources = [anchor(n, "正文" * 40, "题" * 160, "节" * 160) for n in range(3)]
    batches = list(reranker._batches("问题", sources))
    assert [len(batch) for _, batch in batches] == [1, 1, 1]
    assert [item for _, batch in batches for item in batch] == sources
