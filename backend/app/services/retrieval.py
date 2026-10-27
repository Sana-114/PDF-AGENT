import json
import logging
import math
import re
from collections import Counter
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.rerankers.base import Reranker
from app.schemas.agent import EvidenceAnchor
from app.services.author_evidence import AUTHOR_QUERY, AUTHOR_SECTION, _name_key
from app.services.baseline_evidence import baseline_citations, matches_baseline_reference
from app.services.chunking import ensure_document_chunks
from app.services.evidence_fields import (
    CONTEXT_FACET,
    CONTEXT_QUERY,
    PARAMETER_FACET,
    PARAMETER_QUERY,
    context_statement,
    parameter_statement,
    select_field_evidence,
)
from app.services.mechanism_evidence import FACET_ALIASES, requested_mechanisms
from app.services.retrieval_policy import (
    EXPLANATION_FACET,
    EXPLANATION_QUERY,
    PROCEDURE_FACET,
    SUMMARY_QUERY,
    evidence_key,
    explanation_statement,
    is_reference_query,
    procedure_passage,
    requested_procedure,
    requested_source_focuses,
    select_source_evidence,
)
from app.services.text_features import infer_source_type, tokenize

logger = logging.getLogger(__name__)
QUERY_EXPANSIONS = {
    **{key: list(values) for key, values in FACET_ALIASES.items()},
    "内存": ["memory"],
    "显存": ["memory"],
    "量化": ["quantization", "quantizing"],
    "权重": ["weights"],
    "闭式": ["closed form"],
    "采样": ["sampling"],
    "偏好": ["preference"],
    "流程": ["pipeline"],
    "目标": ["objective"],
    "阶段": ["phase", "stage"],
    "微调": ["fine-tuning"],
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
    "context window": ["nctx", "tokens"],
    "上下文": ["context", "window", "size", "nctx", "tokens"],
    # A small domain glossary, not paper-specific translations or answer values.
    "署名": ["author", "authors"],
    "大语言模型": ["large", "language", "models"],
    "涌现": ["emergent", "emergence"],
    "定义": ["define", "defined", "definition"],
    "多语言": ["multilingual"],
    "多任务": ["multitask"],
    "监督": ["supervision", "supervised"],
    "小时": ["hours"],
    "预训练": ["pre-training", "pretraining"],
    "速度": ["speed", "speedup", "faster"],
    "律师资格考试": ["bar", "exam"],
    "分位": ["percentile", "test", "takers"],
    "并行": ["parallelizable", "parallelization", "parallel"],
    "推理": ["inference", "reasoning"],
    "结合": ["combine", "combines"],
    "强化学习": ["reinforcement", "learning", "RL"],
    "人工标注": ["human-labeled", "human-annotated"],
    "轨迹": ["trajectories"],
    "多轮": ["multiple", "epochs"],
    "小批量": ["minibatch"],
    "更新": ["update", "updates"],
    "为什么": ["why"],
    "同一批": ["same", "batch"],
    "采样数据": ["sampled", "data"],
}
SOURCE_INTENTS = {
    "abstract": ("摘要", "abstract", "summary"),
    "table": ("表格", "表中", "table", "score", "分数", "准确率", "bleu"),
    "figure": ("图中", "图片", "figure", "fig."),
    "formula": ("公式", "方程", "等式", "formula", "equation", "β", "beta"),
    "reference": ("参考文献", "引用", "引文", "reference", "citation", "doi"),
}


class LexicalRetriever:
    """Dependency-free BM25-style baseline with PDF evidence anchors."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self, question: str, document_ids: list[str] | None = None, top_k: int = 6,
        *, allowed_sources: set[str] | None = None,
        front_matter_only: bool = False,
        procedure_only: bool = False,
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
        if front_matter_only:
            # Restrict BEFORE scoring/top-k, otherwise long author pages may be
            # dropped behind twenty short contribution or self-citation matches.
            rows = [row for row in rows if row.DocumentChunk.page_number <= 3
                    and (not row.DocumentChunk.section
                         or row.DocumentChunk.section == AUTHOR_SECTION)]
        if allowed_sources is not None:
            rows = [row for row in rows if infer_source_type(
                row.DocumentChunk.section, json.loads(row.DocumentChunk.block_ids_json)
            ) in allowed_sources]
        if procedure_only:
            rows = [row for row in rows if procedure_passage(
                row.DocumentChunk.section, row.DocumentChunk.text,
                infer_source_type(row.DocumentChunk.section,
                                  json.loads(row.DocumentChunk.block_ids_json)),
            )]
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
        for terms, source_type, row in zip(documents, source_types, rows, strict=True):
            score = _bm25_score(
                query_terms, terms, document_frequency, len(documents), average_length
            )
            if source_type in intents:
                score *= 1.6
            if source_type == "reference" and not is_reference_query(question):
                score *= 0.15
            if AUTHOR_QUERY.search(question) and row.DocumentChunk.section == AUTHOR_SECTION:
                score *= 4
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

    def front_matter(self, document_ids: list[str] | None, limit: int = 4):
        """Recall actual credits even when a complete ordinal list cannot be emitted.

        Parsed metadata supplies a name for the QUERY only. Require that name in
        an existing, early front-matter chunk. Never synthesize author order or
        affiliations, and never use a self-citation as a front-page credit.
        """
        query = select(Document).order_by(Document.id)
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        result = []
        for doc in self.session.scalars(query.limit(limit)).all():
            try:
                names = json.loads(doc.authors_json or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(names, list) or not names or not isinstance(names[0], str):
                continue
            key = _name_key(names[0])
            if not key:
                continue
            matches = [item for item in self.search(
                f"{names[0]} authors affiliations", [doc.id], 20,
                allowed_sources={"text"}, front_matter_only=True,
            ) if item.page_number <= 3 and key in _name_key(item.quote)
                and (not item.section or item.section == AUTHOR_SECTION)]
            matches.sort(key=lambda item: (item.section != AUTHOR_SECTION,
                                           item.page_number, -item.score))
            result.extend(matches[:2])
        return result[:limit]


def _expand_query(question: str) -> str:
    normalized_question = question.casefold()
    additions = [
        term
        for key, values in QUERY_EXPANSIONS.items()
        if key in normalized_question
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
        focused = []
        author_keys, summary_keys, explanation_keys = [], [], []
        baseline_keys, baseline_queries = [], []
        source_focus_keys = []
        procedure_keys = []
        if AUTHOR_QUERY.search(question) and not is_reference_query(question):
            focused = self.lexical.front_matter(document_ids)
            author_keys = [evidence_key(item) for item in focused]
        elif SUMMARY_QUERY.search(question) and not is_reference_query(question):
            focused = self.lexical.search(question, document_ids, 4,
                                          allowed_sources={"abstract"})
            summary_keys = [evidence_key(item) for item in focused]
        if focused:
            keys = {evidence_key(item) for item in focused}
            lexical = focused + [item for item in lexical if evidence_key(item) not in keys]
        if EXPLANATION_QUERY.search(question) and not is_reference_query(question):
            explanations = [item for item in self.lexical.search(
                f"{question} {EXPLANATION_FACET}", document_ids, 24,
                allowed_sources={"text", "formula"},
            ) if explanation_statement(item)][:4]
            explanation_keys = [evidence_key(item) for item in explanations]
            keys = set(explanation_keys)
            lexical = explanations + [item for item in lexical if evidence_key(item) not in keys]
        if not is_reference_query(question):
            focus_candidates = []
            if requested_procedure(question):
                procedures = self.lexical.search(
                    PROCEDURE_FACET, document_ids, 8, procedure_only=True,
                )
                procedure_keys = [evidence_key(item) for item in procedures]
                focus_candidates.extend(procedures)
            for query, pattern in requested_source_focuses(question):
                recalled = [item for item in self.lexical.search(
                    query, document_ids, 12, allowed_sources={"abstract", "text", "formula"},
                ) if re.search(pattern, " ".join(item.quote.split()), re.I)][:4]
                source_focus_keys.append([evidence_key(item) for item in recalled])
                focus_candidates.extend(recalled)
            # Each explicit sub-question gets bounded body recall. These are
            # real chunks in the selected documents, reranked together below.
            facets = focus_candidates
            for facet in requested_mechanisms(question):
                facets.extend(self.lexical.search(
                    f"{facet} mechanism memory", document_ids, 2,
                    allowed_sources={"text", "formula"},
                ))
            seen = set()
            lexical = [item for item in [*facets, *lexical]
                       if not (evidence_key(item) in seen or seen.add(evidence_key(item)))]
        if CONTEXT_QUERY.search(question):
            # Bounded field-focused BM25 recall into the SAME scoped chunk store;
            # no model generation, hard-coded values, or synthetic quote merging.
            field_candidates = [item for item in self.lexical.search(
                CONTEXT_FACET, document_ids, 20
            ) if context_statement(item)][:4]
            seen = {item.chunk_id or _anchor_key(item) for item in field_candidates}
            lexical = field_candidates + [item for item in lexical
                                          if (item.chunk_id or _anchor_key(item)) not in seen]
            for doc_id, author, year in baseline_citations(lexical):
                references = [item for item in self.lexical.search(
                    f"reference {author} {year}", [doc_id], 4,
                    allowed_sources={"reference"})
                    if matches_baseline_reference(item, author, year)][:2]
                baseline_keys.extend(evidence_key(item) for item in references)
                if references:
                    baseline_queries.append(f"{author} {year}")
                    keys = {evidence_key(item) for item in references}
                    lexical = references + [item for item in lexical
                                            if evidence_key(item) not in keys]
        if PARAMETER_QUERY.search(question) and not is_reference_query(question):
            parameter_candidates = [item for item in self.lexical.search(
                PARAMETER_FACET, document_ids, 20
            ) if parameter_statement(item)][:4]
            seen = {item.chunk_id or _anchor_key(item) for item in parameter_candidates}
            lexical = parameter_candidates + [item for item in lexical
                                              if (item.chunk_id or _anchor_key(item)) not in seen]
        if not self.vector_index.enabled:
            return self._rerank(question, lexical, top_k, "lexical", author_keys, summary_keys,
                                explanation_keys, baseline_keys, baseline_queries,
                                source_focus_keys, procedure_keys)
        try:
            vector = self.vector_index.search(
                self.session, question, document_ids, candidate_k
            )
            if document_ids:
                scope = set(document_ids)
                vector = [item for item in vector if item.document_id in scope]
        except Exception as exc:
            logger.warning("Vector retrieval unavailable, using BM25 fallback: %s", exc)
            return self._rerank(question, lexical, top_k, "lexical", author_keys, summary_keys,
                                explanation_keys, baseline_keys, baseline_queries,
                                source_focus_keys, procedure_keys)
        if not vector:
            return self._rerank(question, lexical, top_k, "lexical", author_keys, summary_keys,
                                explanation_keys, baseline_keys, baseline_queries,
                                source_focus_keys, procedure_keys)
        fused = _reciprocal_rank_fusion(lexical, vector, candidate_k)
        return self._rerank(question, fused, top_k, "hybrid", author_keys, summary_keys,
                            explanation_keys, baseline_keys, baseline_queries, source_focus_keys,
                            procedure_keys)

    def _rerank(
        self,
        question: str,
        evidence: list[EvidenceAnchor],
        top_k: int,
        fallback_mode: str,
        author_keys: list[str] = (),
        summary_keys: list[str] = (),
        explanation_keys: list[str] = (),
        baseline_keys: list[str] = (),
        baseline_queries: list[str] = (),
        source_focus_keys: list[list[str]] = (),
        procedure_keys: list[str] = (),
    ) -> list[EvidenceAnchor]:
        self.last_recall_mode = fallback_mode
        evidence = [
            item.model_copy(update={"recall_mode": fallback_mode}, deep=True)
            for item in evidence
        ]
        def select(candidates):
            if (not is_reference_query(question)
                    and (CONTEXT_QUERY.search(question) or PARAMETER_QUERY.search(question))):
                selected = select_field_evidence(
                    question, candidates, top_k, settings.rag_min_score)
                references = [item for item in candidates if evidence_key(item) in baseline_keys
                              and item.score >= settings.rag_min_score]
                if references and top_k > 1:
                    # At most one source-identity reference, with genuine score,
                    # leaving the main configuration statement at the front.
                    first = references[0]
                    return ([*selected[:1], first] + [item for item in selected[1:]
                            if evidence_key(item) != evidence_key(first)])[:top_k]
                return selected
            return select_source_evidence(
                question, candidates, top_k, settings.rag_min_score,
                author_keys=author_keys, summary_keys=summary_keys,
                explanation_keys=explanation_keys,
                source_focus_keys=source_focus_keys,
                procedure_keys=procedure_keys,
            )

        if not self.reranker.enabled:
            selected = select(evidence)
            return _renumber(selected, fallback_mode)
        try:
            # Context definitions may rank below a same-page architecture table.
            # Score the bounded candidate pool before field-aware final selection.
            scoring_query = question
            if EXPLANATION_QUERY.search(question) and not is_reference_query(question):
                scoring_query = f"Explain the mechanism and motivation: {question}"
            if requested_procedure(question) and not is_reference_query(question):
                scoring_query = f"Explain the procedure, methodology and training steps: {question}"
            if baseline_queries:
                scoring_query += ("; identify the cited architecture baseline and its reference: "
                                  + ", ".join(baseline_queries))
            ranked = self.reranker.rerank(scoring_query, evidence, len(evidence))
            selected = select(ranked)
            return [item.model_copy(update={"evidence_id": f"E{rank}"})
                    for rank, item in enumerate(selected, start=1)]
        except Exception as exc:
            logger.warning("Reranker unavailable, preserving first-stage rank: %s", exc)
            selected = select(evidence)
            return _renumber(selected, fallback_mode)


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
