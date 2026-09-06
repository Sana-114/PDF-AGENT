import json
import math
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import ensure_document_chunks

WORD_PATTERN = re.compile(r"[a-zA-Z]+(?:[_-]?\d+)?|\d+(?:\.\d+)?|[α-ωΑ-Ωβ₁₂]+")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]+")
QUERY_EXPANSIONS = {
    "作者": ["author", "authors"],
    "机构": ["institution", "affiliation", "university"],
    "超参数": ["hyperparameter"],
    "训练": ["training", "trained"],
    "显卡": ["gpu", "graphics"],
    "参数量": ["parameters"],
    "学习率": ["learning", "rate"],
    "数据集": ["dataset"],
    "引用": ["reference", "references"],
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

        query_terms = _tokenize(_expand_query(question))
        if not query_terms:
            return []
        documents = [_tokenize(row.DocumentChunk.text) for row in rows]
        document_frequency = Counter(
            term for terms in documents for term in set(terms) if term in query_terms
        )
        average_length = sum(len(terms) for terms in documents) / max(len(documents), 1)
        raw_scores = [
            _bm25_score(query_terms, terms, document_frequency, len(documents), average_length)
            for terms in documents
        ]
        ranked = sorted(
            ((score, row) for score, row in zip(raw_scores, rows, strict=True) if score > 0),
            key=lambda item: item[0],
            reverse=True,
        )[:top_k]
        if not ranked:
            return []

        max_score = ranked[0][0]
        unique_query_terms = set(query_terms)
        evidence: list[EvidenceAnchor] = []
        for index, (raw_score, row) in enumerate(ranked, start=1):
            chunk = row.DocumentChunk
            coverage = len(unique_query_terms & set(_tokenize(chunk.text))) / len(
                unique_query_terms
            )
            score = min(1.0, 0.65 * coverage + 0.35 * (raw_score / max_score))
            evidence.append(
                EvidenceAnchor(
                    evidence_id=f"E{index}",
                    document_id=chunk.document_id,
                    document_title=row.title,
                    page_number=chunk.page_number,
                    block_ids=json.loads(chunk.block_ids_json),
                    bbox=json.loads(chunk.bbox_json) if chunk.bbox_json else None,
                    section=chunk.section,
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


def _tokenize(text: str) -> list[str]:
    normalized = text.casefold().replace("β₁", "beta_1").replace("β₂", "beta_2")
    tokens = [match.group(0) for match in WORD_PATTERN.finditer(normalized)]
    for match in CJK_PATTERN.finditer(normalized):
        sequence = match.group(0)
        tokens.extend(sequence)
        tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


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
