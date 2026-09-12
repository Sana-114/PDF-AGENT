import json
import logging
import math
from collections import Counter
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.rerankers.base import Reranker
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import ensure_document_chunks
from app.services.text_features import infer_source_type, tokenize

logger = logging.getLogger(__name__)
QUERY_EXPANSIONS = {
    "作者": ["author", "authors"],
    "机构": ["institution", "affiliation", "university"],
    "超参数": ["hyperparameter"],
    "训练": ["training", "trained"],
    "显卡": ["gpu", "graphics"],
    "参数量": ["parameters"],
    "学习率": ["learning", "rate"],
    "数据集": ["dataset"],
    "引用": ["reference", "references", "citation"],
    "参考文献": ["reference", "references", "bibliography"],
    "表格": ["table", "score", "result"],
    "公式": ["formula", "equation"],
    "摘要": ["abstract", "summary"],
}
SOURCE_INTENTS = {
    "abstract": ("摘要", "abstract", "summary"),
    "table": ("表格", "表中", "table", "score", "分数", "准确率", "bleu"),
    "figure": ("图中", "图片", "figure", "fig."),
    "formula": ("公式", "方程", "等式", "formula", "equation", "β", "beta"),
    "reference": ("参考文献", "引用", "文献", "reference", "citation", "doi"),
}


class LexicalRetriever:
    """Dependency-free BM25-style baseline with PDF evidence anchors."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self, question: str, document_ids: list[str] | None = None, top_k: int = 6
    ) -> list[EvidenceAnchor]:
        ensure_document_chunks(self.session, document_ids)
        query = (
            select(DocumentChunk, Document.title)
            .join(Document, Document.id == DocumentChunk.document_id)
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        )
        if document_ids:
            query = query.where(DocumentChunk.document_id.in_(document_ids))
        rows = self.session.execute(query).all()
        if not rows:
            return []

        query_terms = tokenize(_expand_query(question))
        if not query_terms:
            return []
        documents = [
            tokenize(f"{row.DocumentChunk.section or ''} {row.DocumentChunk.text}")
            for row in rows
        ]
        document_frequency = Counter(
            term for terms in documents for term in set(terms) if term in query_terms
        )
        average_length = sum(len(terms) for terms in documents) / max(len(documents), 1)
        source_types = [
            infer_source_type(
                row.DocumentChunk.section, json.loads(row.DocumentChunk.block_ids_json)
            )
            for row in rows
        ]
        intents = _query_source_intents(question)
        raw_scores = []
        for terms, source_type in zip(documents, source_types, strict=True):
            score = _bm25_score(
                query_terms, terms, document_frequency, len(documents), average_length
            )
            if source_type in intents:
                score *= 1.6
            raw_scores.append(score)
        ranked = sorted(
            (
                (score, row, terms)
                for score, row, terms in zip(raw_scores, rows, documents, strict=True)
                if score > 0
            ),
            key=lambda item: item[0],
            reverse=True,
        )[:top_k]
        if not ranked:
            return []

        max_score = ranked[0][0]
        unique_query_terms = set(query_terms)
        evidence: list[EvidenceAnchor] = []
        for index, (raw_score, row, terms) in enumerate(ranked, start=1):
            chunk = row.DocumentChunk
            coverage = len(unique_query_terms & set(terms)) / len(unique_query_terms)
            score = min(1.0, 0.65 * coverage + 0.35 * (raw_score / max_score))
            evidence.append(
                EvidenceAnchor(
                    evidence_id=f"E{index}",
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_title=row.title,
                    page_number=chunk.page_number,
                    block_ids=json.loads(chunk.block_ids_json),
                    bbox=json.loads(chunk.bbox_json) if chunk.bbox_json else None,
                    section=chunk.section,
                    source_type=infer_source_type(
                        chunk.section, json.loads(chunk.block_ids_json)
                    ),
                    retrieval_mode="lexical",
                    quote=chunk.text,
                    score=round(score, 4),
                )
            )
        return evidence


def _expand_query(question: str) -> str:
    additions = [
        term
        for key, values in QUERY_EXPANSIONS.items()
        if key in question
        for term in values
    ]
    return " ".join([question, *additions])


def _query_source_intents(question: str) -> set[str]:
    normalized = question.casefold()
    return {
        source_type
        for source_type, hints in SOURCE_INTENTS.items()
        if any(hint in normalized for hint in hints)
    }


class VectorIndex(Protocol):
    enabled: bool

    def search(
        self,
        session: Session,
        question: str,
        document_ids: list[str] | None,
        limit: int,
    ) -> list[EvidenceAnchor]: ...


class HybridRetriever:
    """Fuse the proven BM25 rank with Qdrant dense+sparse RRF candidates."""

    def __init__(
        self,
        session: Session,
        vector_index: VectorIndex | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.session = session
        self.lexical = LexicalRetriever(session)
        if vector_index is None:
            from app.services.vector_index import get_vector_index

            vector_index = get_vector_index()
        self.vector_index = vector_index
        if reranker is None:
            from app.rerankers import get_reranker

            reranker = get_reranker()
        self.reranker = reranker

    def search(
        self, question: str, document_ids: list[str] | None = None, top_k: int = 6
    ) -> list[EvidenceAnchor]:
        candidate_k = max(20, top_k * 3)
        if self.reranker.enabled:
            candidate_k = max(candidate_k, self.reranker.candidate_k)
        lexical = self.lexical.search(question, document_ids, candidate_k)
        if not self.vector_index.enabled:
            return self._rerank(question, lexical, top_k, "lexical")
        try:
            vector = self.vector_index.search(
                self.session, question, document_ids, candidate_k
            )
        except Exception as exc:
            logger.warning("Vector retrieval unavailable, using BM25 fallback: %s", exc)
            return self._rerank(question, lexical, top_k, "lexical")
        if not vector:
            return self._rerank(question, lexical, top_k, "lexical")
        fused = _reciprocal_rank_fusion(lexical, vector, candidate_k)
        return self._rerank(question, fused, top_k, "hybrid")

    def _rerank(
        self,
        question: str,
        evidence: list[EvidenceAnchor],
        top_k: int,
        fallback_mode: str,
    ) -> list[EvidenceAnchor]:
        if not self.reranker.enabled:
            return _renumber(evidence[:top_k], fallback_mode)
        try:
            return self.reranker.rerank(question, evidence, top_k)
        except Exception as exc:
            logger.warning("Reranker unavailable, preserving first-stage rank: %s", exc)
            return _renumber(evidence[:top_k], fallback_mode)


def _reciprocal_rank_fusion(
    lexical: list[EvidenceAnchor], vector: list[EvidenceAnchor], top_k: int
) -> list[EvidenceAnchor]:
    anchors: dict[str, EvidenceAnchor] = {}
    scores: Counter[str] = Counter()
    original_scores: dict[str, float] = {}
    for ranking, weight in ((lexical, 2.0), (vector, 1.0)):
        for rank, anchor in enumerate(ranking, start=1):
            key = anchor.chunk_id or _anchor_key(anchor)
            scores[key] += weight / (60 + rank)
            original_scores[key] = max(original_scores.get(key, 0.0), anchor.score)
            if key not in anchors or anchor.retrieval_mode == "vector":
                anchors[key] = anchor
    if not scores:
        return []
    max_fused = max(scores.values())
    final_scores = {
        key: min(1.0, 0.7 * score / max_fused + 0.3 * original_scores[key])
        for key, score in scores.items()
    }
    ranked_keys = sorted(final_scores, key=final_scores.get, reverse=True)[:top_k]
    results = []
    for index, key in enumerate(ranked_keys, start=1):
        anchor = anchors[key].model_copy(deep=True)
        anchor.evidence_id = f"E{index}"
        anchor.retrieval_mode = "hybrid"
        anchor.score = round(final_scores[key], 4)
        results.append(anchor)
    return results


def _renumber(evidence: list[EvidenceAnchor], mode: str) -> list[EvidenceAnchor]:
    results = []
    for index, item in enumerate(evidence, start=1):
        copy = item.model_copy(deep=True)
        copy.evidence_id = f"E{index}"
        copy.retrieval_mode = mode
        results.append(copy)
    return results


def _anchor_key(anchor: EvidenceAnchor) -> str:
    return "|".join(
        [anchor.document_id, str(anchor.page_number), *anchor.block_ids, anchor.quote]
    )


def _bm25_score(
    query_terms: list[str],
    document_terms: list[str],
    document_frequency: Counter[str],
    document_count: int,
    average_length: float,
) -> float:
    frequencies = Counter(document_terms)
    score = 0.0
    k1 = 1.5
    b = 0.75
    for term in set(query_terms):
        frequency = frequencies[term]
        if not frequency:
            continue
        doc_frequency = document_frequency[term]
        inverse_frequency = math.log(
            1 + (document_count - doc_frequency + 0.5) / (doc_frequency + 0.5)
        )
        denominator = frequency + k1 * (
            1 - b + b * len(document_terms) / max(average_length, 1)
        )
        score += inverse_frequency * frequency * (k1 + 1) / denominator
    return score
