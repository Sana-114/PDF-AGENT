import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.models.document import Document
from app.schemas.citation_graph import (
    CitationGraphEdgeRead,
    CitationGraphNodeRead,
    CitationGraphRead,
    CitationGraphStatsRead,
)
from app.services.document_content import read_parsed_document, reference_links

ARXIV_ID = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", re.IGNORECASE)
TITLE_TERM = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)
STOP_TERMS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "toward",
    "towards",
    "via",
    "with",
}


@dataclass(slots=True)
class _PaperIdentity:
    document: Document
    title: str
    normalized_title: str
    terms: set[str]
    arxiv_id: str | None


@dataclass(slots=True)
class _ReferenceMatch:
    target_id: str
    score: float
    reason: str


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(TITLE_TERM.findall(normalized))


def title_terms(value: str) -> set[str]:
    return {term for term in normalize_title(value).split() if term not in STOP_TERMS}


def _normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    match = ARXIV_ID.search(value)
    return match.group(1).lower() if match else value.lower().removeprefix("arxiv:")


def _document_title(document: Document, parsed: dict[str, Any] | None) -> str:
    parsed_title = str((parsed or {}).get("title") or "").strip()
    return (document.title or parsed_title or Path(document.original_filename).stem).strip()


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def match_local_reference(
    reference_text: str,
    source_document_id: str,
    papers: Sequence[_PaperIdentity],
    *,
    threshold: float = 0.72,
) -> _ReferenceMatch | None:
    normalized_reference = normalize_title(reference_text)
    reference_terms = title_terms(reference_text)
    reference_arxiv_ids = {item.lower() for item in ARXIV_ID.findall(reference_text)}
    best: _ReferenceMatch | None = None

    for paper in papers:
        if paper.document.id == source_document_id:
            continue
        if paper.arxiv_id and paper.arxiv_id in reference_arxiv_ids:
            candidate = _ReferenceMatch(paper.document.id, 1.0, "arXiv ID 完全一致")
        else:
            if len(paper.normalized_title.replace(" ", "")) < 10 or len(paper.terms) < 3:
                continue
            compact_title = paper.normalized_title.replace(" ", "")
            compact_reference = normalized_reference.replace(" ", "")
            if compact_title in compact_reference:
                candidate = _ReferenceMatch(paper.document.id, 0.98, "参考文献包含完整题名")
            else:
                overlap = len(paper.terms & reference_terms)
                coverage = overlap / max(len(paper.terms), 1)
                precision = overlap / max(len(reference_terms), 1)
                similarity = SequenceMatcher(
                    None, paper.normalized_title, normalized_reference
                ).ratio()
                score = 0.7 * coverage + 0.2 * precision + 0.1 * similarity
                candidate = _ReferenceMatch(
                    paper.document.id,
                    round(score, 4),
                    f"题名词覆盖 {coverage:.0%}，文本相似度 {similarity:.0%}",
                )
        if candidate.score >= threshold and (best is None or candidate.score > best.score):
            best = candidate
    return best


def _pagerank(
    document_ids: list[str],
    relation_weights: Counter[tuple[str, str]],
    *,
    damping: float = 0.85,
    iterations: int = 60,
) -> dict[str, float]:
    if not document_ids:
        return {}
    count = len(document_ids)
    rank = {document_id: 1 / count for document_id in document_ids}
    outgoing: dict[str, Counter[str]] = defaultdict(Counter)
    for (source_id, target_id), weight in relation_weights.items():
        outgoing[source_id][target_id] += weight

    for _ in range(iterations):
        updated = {document_id: (1 - damping) / count for document_id in document_ids}
        for source_id in document_ids:
            targets = outgoing.get(source_id)
            total_weight = sum(targets.values()) if targets else 0
            if total_weight == 0:
                share = damping * rank[source_id] / count
                for target_id in document_ids:
                    updated[target_id] += share
                continue
            for target_id, weight in targets.items():
                updated[target_id] += damping * rank[source_id] * weight / total_weight
        if max(abs(updated[item] - rank[item]) for item in document_ids) < 1e-9:
            rank = updated
            break
        rank = updated
    return rank


class CitationGraphService:
    def build(
        self,
        documents: Sequence[Document],
        *,
        match_threshold: float = 0.72,
    ) -> CitationGraphRead:
        parsed_by_id: dict[str, dict[str, Any] | None] = {}
        papers: list[_PaperIdentity] = []
        warnings: list[str] = []
        for document in documents:
            parsed = read_parsed_document(document.id)
            parsed_by_id[document.id] = parsed
            title = _document_title(document, parsed)
            papers.append(
                _PaperIdentity(
                    document=document,
                    title=title,
                    normalized_title=normalize_title(title),
                    terms=title_terms(title),
                    arxiv_id=_normalize_arxiv_id(document.arxiv_id),
                )
            )
            if parsed is None:
                warnings.append(f"《{title}》缺少结构化解析结果，已保留节点但无法读取出边。")

        relation_payloads: dict[tuple[str, str], dict[str, Any]] = {}
        relation_weights: Counter[tuple[str, str]] = Counter()
        total_references = 0
        matched_references = 0
        for paper in papers:
            parsed = parsed_by_id[paper.document.id]
            if parsed is None:
                continue
            references = [
                item
                for item in parsed.get("references", [])
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            ]
            _, mentions = reference_links(parsed)
            mention_counts = Counter(str(item["label"]) for item in mentions)
            total_references += len(references)
            for index, reference in enumerate(references, start=1):
                match = match_local_reference(
                    str(reference["text"]),
                    paper.document.id,
                    papers,
                    threshold=match_threshold,
                )
                if match is None:
                    continue
                matched_references += 1
                key = (paper.document.id, match.target_id)
                relation_weights[key] += 1
                payload = relation_payloads.setdefault(
                    key,
                    {
                        "reference_ids": [],
                        "reference_labels": [],
                        "reference_pages": [],
                        "sample_reference": str(reference["text"])[:1000],
                        "mention_count": 0,
                        "match_score": match.score,
                        "match_reason": match.reason,
                    },
                )
                reference_id = str(reference.get("reference_id") or f"ref-{index}")
                label = str(reference.get("label") or index)
                page_number = max(1, int(reference.get("page_number") or 1))
                payload["reference_ids"].append(reference_id)
                payload["reference_labels"].append(label)
                payload["reference_pages"].append(page_number)
                payload["mention_count"] += mention_counts[label]
                if match.score > payload["match_score"]:
                    payload["match_score"] = match.score
                    payload["match_reason"] = match.reason

        document_ids = [paper.document.id for paper in papers]
        incoming: dict[str, set[str]] = defaultdict(set)
        outgoing: dict[str, set[str]] = defaultdict(set)
        for source_id, target_id in relation_weights:
            outgoing[source_id].add(target_id)
            incoming[target_id].add(source_id)
        ranks = _pagerank(document_ids, relation_weights)
        max_rank = max(ranks.values(), default=1.0)
        max_in_degree = max((len(incoming[item]) for item in document_ids), default=1) or 1
        foundation_scores = {
            document_id: round(
                0.65 * ranks.get(document_id, 0.0) / max_rank
                + 0.35 * len(incoming[document_id]) / max_in_degree,
                4,
            )
            for document_id in document_ids
        }
        ranked_foundations = sorted(
            (item for item in document_ids if incoming[item]),
            key=lambda item: (foundation_scores[item], len(incoming[item]), item),
            reverse=True,
        )
        cornerstone_limit = max(1, math.ceil(len(document_ids) * 0.2))
        cornerstone_ids = set(ranked_foundations[:cornerstone_limit])

        nodes: list[CitationGraphNodeRead] = []
        for paper in papers:
            document_id = paper.document.id
            in_degree = len(incoming[document_id])
            out_degree = len(outgoing[document_id])
            if document_id in cornerstone_ids:
                role = "cornerstone"
            elif in_degree and out_degree:
                role = "bridge"
            elif out_degree and not in_degree:
                role = "derivative"
            elif not in_degree and not out_degree:
                role = "isolated"
            else:
                role = "peripheral"
            nodes.append(
                CitationGraphNodeRead(
                    document_id=document_id,
                    title=paper.title,
                    authors=_json_list(paper.document.authors_json),
                    arxiv_id=paper.document.arxiv_id,
                    arxiv_version=paper.document.arxiv_version,
                    page_count=paper.document.page_count,
                    in_degree=in_degree,
                    out_degree=out_degree,
                    pagerank=round(ranks.get(document_id, 0.0), 6),
                    foundation_score=foundation_scores[document_id],
                    role=role,
                )
            )
        nodes.sort(key=lambda item: (-item.foundation_score, item.title.casefold()))

        edges = [
            CitationGraphEdgeRead(
                edge_id=f"{source_id}:{target_id}",
                source_document_id=source_id,
                target_document_id=target_id,
                **payload,
            )
            for (source_id, target_id), payload in sorted(relation_payloads.items())
        ]
        possible_relations = len(document_ids) * max(len(document_ids) - 1, 0)
        density = len(edges) / possible_relations if possible_relations else 0.0
        return CitationGraphRead(
            generated_at=datetime.now(UTC),
            nodes=nodes,
            edges=edges,
            stats=CitationGraphStatsRead(
                document_count=len(nodes),
                relation_count=len(edges),
                total_references=total_references,
                matched_references=matched_references,
                unmatched_references=total_references - matched_references,
                cornerstone_count=sum(item.role == "cornerstone" for item in nodes),
                derivative_count=sum(item.role == "derivative" for item in nodes),
                density=round(density, 6),
            ),
            warnings=warnings,
        )


citation_graph = CitationGraphService()
