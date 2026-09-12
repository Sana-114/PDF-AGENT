import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMProvider, LLMResponseError
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document
from app.models.paper_source import PaperSource
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.schemas.review import (
    FutureDirectionRead,
    ResearchReviewRead,
    ReviewEvidenceRead,
    ReviewPaperRead,
)
from app.services.citation_graph import CitationGraphService
from app.services.document_content import read_parsed_document

FUTURE_HEADING = re.compile(
    r"(?:future\s+(?:work|research|direction)|limitations?|open\s+(?:questions?|problems?)|"
    r"outlook|展望|未来(?:工作|研究|方向)|局限|开放问题)",
    re.IGNORECASE,
)
OVERVIEW_HEADING = re.compile(
    r"^(?:abstract|摘要|introduction|引言|conclusions?|结论|discussion|讨论)",
    re.IGNORECASE,
)
FUTURE_SIGNAL = re.compile(
    r"(?:future\s+(?:work|research)|in\s+future|remain(?:s|ing)?\s+(?:to|an?\s+open)|"
    r"further\s+(?:work|research|study)|open\s+(?:question|problem)|we\s+(?:plan|hope)|"
    r"limitation|未来(?:工作|研究|方向)|后续(?:工作|研究)|尚待|有待|仍需|局限)",
    re.IGNORECASE,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
YEAR = re.compile(r"(?:19|20)\d{2}")
GENERIC_DIRECTION_TERMS = {
    "future",
    "work",
    "research",
    "further",
    "study",
    "open",
    "question",
    "problem",
    "remain",
    "remains",
    "remaining",
    "plan",
    "hope",
    "limitation",
    "limitations",
    "should",
    "could",
    "would",
    "will",
    "our",
    "we",
    "未来",
    "工作",
    "研究",
    "方向",
    "后续",
    "仍需",
    "有待",
    "尚待",
    "我们",
}


@dataclass(slots=True)
class _EvidenceDraft:
    document_id: str
    document_title: str
    publication_year: int | None
    page_number: int
    block_ids: list[str]
    bbox: list[float] | None
    section: str | None
    quote: str
    kind: str


def _publication_year(document: Document, source: PaperSource | None) -> int | None:
    if source:
        try:
            metadata = json.loads(source.metadata_json)
        except (TypeError, ValueError):
            metadata = {}
        for key in ("year", "published_at"):
            match = YEAR.search(str(metadata.get(key) or ""))
            if match:
                return int(match.group())
    arxiv_id = document.arxiv_id or ""
    if re.fullmatch(r"\d{4}\.\d{4,5}", arxiv_id):
        short_year = int(arxiv_id[:2])
        return 2000 + short_year
    return None


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return [float(item) for item in value]


def _extract_drafts(
    document: Document,
    parsed: dict[str, Any],
    publication_year: int | None,
) -> list[_EvidenceDraft]:
    title = document.title or str(parsed.get("title") or document.original_filename)
    overview: list[_EvidenceDraft] = []
    future: list[_EvidenceDraft] = []
    current_section: str | None = None
    current_kind: str | None = None

    for page in parsed.get("pages", []):
        page_number = max(1, int(page.get("page_number") or 1))
        for block in page.get("blocks", []):
            text = " ".join(str(block.get("text") or "").split())
            if not text:
                continue
            if block.get("type") == "heading":
                current_section = text[:500]
                if FUTURE_HEADING.search(text):
                    current_kind = "future_work"
                elif OVERVIEW_HEADING.search(text):
                    current_kind = "overview"
                else:
                    current_kind = None
                continue
            kind = "future_work" if FUTURE_SIGNAL.search(text) else current_kind
            if kind not in {"overview", "future_work"}:
                continue
            target = future if kind == "future_work" else overview
            if len(target) >= (4 if kind == "future_work" else 2):
                continue
            target.append(
                _EvidenceDraft(
                    document_id=document.id,
                    document_title=title,
                    publication_year=publication_year,
                    page_number=page_number,
                    block_ids=[str(block.get("block_id"))] if block.get("block_id") else [],
                    bbox=_valid_bbox(block.get("bbox")),
                    section=current_section,
                    quote=text[:1600],
                    kind=kind,
                )
            )

    abstract = " ".join(str(parsed.get("abstract") or "").split())
    if abstract and not overview:
        overview.append(
            _EvidenceDraft(
                document_id=document.id,
                document_title=title,
                publication_year=publication_year,
                page_number=1,
                block_ids=[],
                bbox=None,
                section="Abstract",
                quote=abstract[:1600],
                kind="overview",
            )
        )
    if not overview:
        for page in parsed.get("pages", [])[:2]:
            for block in page.get("blocks", []):
                text = " ".join(str(block.get("text") or "").split())
                if block.get("type") == "heading" or len(text) < 80:
                    continue
                overview.append(
                    _EvidenceDraft(
                        document_id=document.id,
                        document_title=title,
                        publication_year=publication_year,
                        page_number=max(1, int(page.get("page_number") or 1)),
                        block_ids=[str(block.get("block_id"))]
                        if block.get("block_id")
                        else [],
                        bbox=_valid_bbox(block.get("bbox")),
                        section=None,
                        quote=text[:1600],
                        kind="overview",
                    )
                )
                break
            if overview:
                break
    return [*overview, *future]


def _direction_sentence(quote: str) -> str:
    sentences = [item.strip() for item in SENTENCE_SPLIT.split(quote) if item.strip()]
    selected = next((item for item in sentences if FUTURE_SIGNAL.search(item)), quote.strip())
    return selected[:600]


def _concept_terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    english_terms = {
        item[:-1] if len(item) > 4 and item.endswith("s") else item
        for item in re.findall(r"[a-z0-9]+", normalized)
        if item not in GENERIC_DIRECTION_TERMS and len(item) > 2
    }
    chinese_terms = {
        sequence[index : index + 2]
        for sequence in re.findall(r"[\u3400-\u9fff]{2,}", normalized)
        for index in range(len(sequence) - 1)
        if sequence[index : index + 2] not in GENERIC_DIRECTION_TERMS
    }
    return english_terms | chinese_terms


def _later_progress(
    direction: str,
    source_document_id: str,
    source_year: int | None,
    papers: Sequence[ReviewPaperRead],
    overview_by_document: dict[str, list[ReviewEvidenceRead]],
    graph_edges: set[tuple[str, str]],
) -> tuple[list[str], list[str], float]:
    if source_year is None:
        return [], [], 0.0
    direction_terms = _concept_terms(direction)
    if len(direction_terms) < 2:
        return [], [], 0.0
    matches: list[tuple[float, str, str]] = []
    for paper in papers:
        if paper.document_id == source_document_id:
            continue
        if paper.publication_year is None or paper.publication_year <= source_year:
            continue
        for evidence in overview_by_document.get(paper.document_id, []):
            overlap = len(direction_terms & _concept_terms(evidence.quote))
            coverage = overlap / len(direction_terms)
            graph_bonus = 0.15 if (paper.document_id, source_document_id) in graph_edges else 0.0
            score = 0.8 * coverage + graph_bonus + 0.05
            if coverage >= 0.45 and score >= 0.55:
                matches.append((score, paper.document_id, evidence.evidence_id))
    matches.sort(reverse=True)
    return (
        list(dict.fromkeys(item[1] for item in matches[:3])),
        list(dict.fromkeys(item[2] for item in matches[:3])),
        matches[0][0] if matches else 0.0,
    )


class EvidenceReviewService:
    def __init__(
        self,
        session: Session,
        *,
        provider: LLMProvider | None = None,
    ) -> None:
        self.session = session
        self.provider = provider

    async def generate(
        self,
        documents: Sequence[Document],
        *,
        match_threshold: float = 0.72,
        max_evidence: int = 30,
    ) -> ResearchReviewRead:
        graph = CitationGraphService().build(documents, match_threshold=match_threshold)
        graph_nodes = {item.document_id: item for item in graph.nodes}
        sources = {
            item.document_id: item
            for item in self.session.scalars(
                select(PaperSource).where(
                    PaperSource.document_id.in_([doc.id for doc in documents])
                )
            ).all()
        }
        drafts: list[_EvidenceDraft] = []
        years: dict[str, int | None] = {}
        warnings = list(graph.warnings)
        for document in documents:
            years[document.id] = _publication_year(document, sources.get(document.id))
            parsed = read_parsed_document(document.id)
            if parsed is None:
                continue
            drafts.extend(_extract_drafts(document, parsed, years[document.id]))
        drafts = drafts[:max_evidence]
        evidence = [
            ReviewEvidenceRead(
                evidence_id=f"R{index}",
                chunk_id=None,
                document_id=item.document_id,
                document_title=item.document_title,
                page_number=item.page_number,
                block_ids=item.block_ids,
                bbox=item.bbox,
                section=item.section,
                source_type="abstract" if item.section == "Abstract" else "text",
                retrieval_mode="lexical",
                quote=item.quote,
                score=1.0,
                evidence_kind=item.kind,
            )
            for index, item in enumerate(drafts, start=1)
        ]
        evidence_by_document: dict[str, list[ReviewEvidenceRead]] = {}
        for item in evidence:
            evidence_by_document.setdefault(item.document_id, []).append(item)
        overview_by_document = {
            document_id: [item for item in items if item.evidence_kind == "overview"]
            for document_id, items in evidence_by_document.items()
        }
        papers = [
            ReviewPaperRead(
                document_id=document.id,
                title=graph_nodes[document.id].title,
                publication_year=years[document.id],
                graph_role=graph_nodes[document.id].role,
                foundation_score=graph_nodes[document.id].foundation_score,
                overview_evidence_ids=[
                    item.evidence_id
                    for item in evidence_by_document.get(document.id, [])
                    if item.evidence_kind == "overview"
                ],
                future_work_evidence_ids=[
                    item.evidence_id
                    for item in evidence_by_document.get(document.id, [])
                    if item.evidence_kind == "future_work"
                ],
            )
            for document in documents
        ]
        papers.sort(
            key=lambda item: (
                item.publication_year is None,
                item.publication_year or 9999,
                -item.foundation_score,
            )
        )
        graph_edges = {
            (item.source_document_id, item.target_document_id) for item in graph.edges
        }
        directions: list[FutureDirectionRead] = []
        for item in evidence:
            if item.evidence_kind != "future_work":
                continue
            direction = _direction_sentence(item.quote)
            source_year = years[item.document_id]
            addressed_by, progress_ids, progress_score = _later_progress(
                direction,
                item.document_id,
                source_year,
                papers,
                overview_by_document,
                graph_edges,
            )
            status = "possibly_addressed" if addressed_by else "open"
            foundation = graph_nodes[item.document_id].foundation_score
            exploration_score = (
                max(0.05, 0.25 - min(progress_score, 1.0) * 0.2)
                if addressed_by
                else min(1.0, 0.55 + 0.3 * foundation)
            )
            reason = (
                "本地较新论文存在主题重合的进展证据，需人工确认是否已完全解决。"
                if addressed_by
                else "当前本地语料未发现更晚且高重合的进展证据，保留为待探索方向。"
            )
            directions.append(
                FutureDirectionRead(
                    direction_id=f"F{len(directions) + 1}",
                    text=direction,
                    source_document_id=item.document_id,
                    source_title=item.document_title or item.document_id,
                    source_year=source_year,
                    source_evidence_id=item.evidence_id,
                    status=status,
                    possibly_addressed_by_document_ids=addressed_by,
                    progress_evidence_ids=progress_ids,
                    exploration_score=round(exploration_score, 4),
                    reason=reason,
                )
            )
        directions.sort(
            key=lambda item: (
                item.status == "possibly_addressed",
                -item.exploration_score,
                item.source_year or 9999,
            )
        )

        provider = self.provider
        if provider is None:
            try:
                provider = get_llm_provider()
            except LLMConfigurationError as exc:
                warnings.append(f"LLM 配置不可用，已使用抽取式综述：{exc}")
                provider = ExtractiveProvider()
        grounding = [EvidenceAnchor.model_validate(item.model_dump()) for item in evidence]
        question = (
            "请基于给定多篇论文证据生成简短中文领域综述：说明研究脉络、核心贡献与局域"
            "引用关系。只陈述证据支持的内容，并在每项事实后引用证据 ID。不要把相似主题"
            "直接声称为已解决；Future Work 的状态由系统单独提供。"
        )
        try:
            generated = await provider.generate_grounded_answer(question, grounding)
        except (LLMConfigurationError, LLMResponseError) as exc:
            warnings.append(f"生成式综述不可用，已使用抽取式综述：{exc}")
            provider = ExtractiveProvider()
            generated = await provider.generate_grounded_answer(question, grounding)
        allowed_ids = {item.evidence_id for item in evidence}
        claims = [
            AnswerClaim(
                text=claim.text,
                evidence_ids=[item for item in claim.evidence_ids if item in allowed_ids],
            )
            for claim in generated.claims
            if claim.text and any(item in allowed_ids for item in claim.evidence_ids)
        ]
        return ResearchReviewRead(
            generated_at=datetime.now(UTC),
            review=generated.answer,
            claims=claims,
            papers=papers,
            future_directions=directions,
            evidence=evidence,
            graph_stats=graph.stats,
            provider=provider.name,
            model=provider.model,
            insufficient_evidence=not claims,
            warnings=warnings,
        )
