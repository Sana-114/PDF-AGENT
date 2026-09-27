from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from time import perf_counter
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import get_llm_provider
from app.llm.base import (
    LLMConfigurationError,
    LLMInvalidCitationError,
    LLMProvider,
    LLMResponseError,
)
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document
from app.schemas.agent import AgentTraceStep, EvidenceAnchor
from app.schemas.comparison import (
    ComparisonAuditRead,
    ComparisonClaimRead,
    ComparisonMatrixCellRead,
    ComparisonMatrixRowRead,
    ComparisonPaperRead,
    ComparisonStatsRead,
    CrossPaperComparisonRead,
)
from app.services.retrieval import HybridRetriever
from app.services.text_features import tokenize

CLAIM_PREFIX_PATTERN = re.compile(
    r"^\s*\[(AGREEMENT|DIFFERENCE|CONFLICT|SINGLE_SOURCE)\]\s*"
    r"(?:\[(?:DIMENSION|维度)\s*:\s*([^\]]{1,80})\]\s*)?",
    re.IGNORECASE,
)
NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![\w-])\d+(?:[.,]\d+)*(?:\s?(?:%|[KMB]|thousand|million|billion))?(?![\w])",
    re.IGNORECASE,
)
RELATION_MAP = {
    "agreement": "agreement",
    "difference": "difference",
    "conflict": "conflict",
    "single_source": "single_source",
}


class ComparisonRetriever(Protocol):
    def search(
        self,
        question: str,
        document_ids: list[str] | None = None,
        top_k: int = 6,
    ) -> list[EvidenceAnchor]: ...


class PaperComparisonService:
    def __init__(
        self,
        session: Session,
        *,
        retriever: ComparisonRetriever | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.session = session
        self.retriever = retriever or HybridRetriever(session)
        self.provider = provider

    async def compare(
        self,
        documents: list[Document],
        question: str,
        *,
        evidence_per_document: int = 4,
    ) -> CrossPaperComparisonRead:
        evidence: list[EvidenceAnchor] = []
        papers: list[ComparisonPaperRead] = []
        trace: list[AgentTraceStep] = []
        warnings: list[str] = []
        facets = _comparison_facets(question)

        for document in documents:
            started = perf_counter()
            candidate_limit = min(12, max(evidence_per_document * 3, evidence_per_document))
            candidates = self.retriever.search(
                question,
                document_ids=[document.id],
                top_k=candidate_limit,
            )
            facet_candidates = [
                self.retriever.search(
                    facet,
                    document_ids=[document.id],
                    top_k=max(4, evidence_per_document * 2),
                )
                for facet in facets[:evidence_per_document]
            ]
            raw_strong = [
                item
                for group in [candidates, *facet_candidates]
                for item in group
                if item.score >= settings.rag_min_score
            ]
            strong = _unique_evidence(raw_strong) if facets else raw_strong
            selected = _select_faceted_evidence(facet_candidates, strong, evidence_per_document)
            document_evidence: list[EvidenceAnchor] = []
            for item in selected:
                copy = item.model_copy(deep=True)
                copy.evidence_id = f"E{len(evidence) + 1}"
                if not copy.document_title:
                    copy.document_title = _document_title(document)
                evidence.append(copy)
                document_evidence.append(copy)
            title = _document_title(document)
            papers.append(
                ComparisonPaperRead(
                    document_id=document.id,
                    title=title,
                    evidence_ids=[item.evidence_id for item in document_evidence],
                    evidence_count=len(document_evidence),
                    candidate_count=len(strong),
                    evidence_page_count=len({item.page_number for item in document_evidence}),
                    source_types=list(
                        dict.fromkeys(item.source_type for item in document_evidence)
                    ),
                    coverage="supported" if document_evidence else "no_evidence",
                )
            )
            trace.append(
                AgentTraceStep(
                    skill="search_evidence",
                    status="ok" if document_evidence else "insufficient",
                    summary=(
                        f"《{title}》从 {len(strong)} 条强候选中保留 "
                        f"{len(document_evidence)} 条跨页/类型去重证据。"
                    ),
                    duration_ms=_elapsed_ms(started),
                )
            )

        supported_count = sum(item.coverage == "supported" for item in papers)
        if supported_count < 2:
            warnings.append("至少两篇论文需要命中强证据，当前结果不能形成可靠跨文献比较。")
            provider = self._provider(warnings)
            return CrossPaperComparisonRead(
                question=question,
                answer="当前只有不足两篇论文命中可验证证据，无法进行可靠的跨论文对比。",
                papers=papers,
                claims=[],
                matrix=[],
                evidence=evidence,
                stats=ComparisonStatsRead(supported_document_count=supported_count),
                audit=ComparisonAuditRead(),
                insufficient_evidence=True,
                provider=provider.name,
                model=provider.model,
                trace=trace,
                warnings=warnings,
            )

        provider = self._provider(warnings)
        generation_started = perf_counter()
        generation_status = "ok"
        prompt = _comparison_prompt(question, papers)
        generation_attempts = 1
        try:
            generated = await provider.generate_grounded_answer(prompt, evidence)
        except LLMInvalidCitationError:
            generation_attempts = 2
            warnings.append("模型首次返回的证据编号无效，已按原文白名单重试一次。")
            retry_prompt = (
                prompt
                + "\nRetry: each claim's evidence_ids array must contain exact IDs from: "
                + ", ".join(item.evidence_id for item in evidence)
                + ". Never invent an ID or leave a factual claim uncited."
            )
            try:
                generated = await provider.generate_grounded_answer(retry_prompt, evidence)
            except (LLMConfigurationError, LLMResponseError) as exc:
                warnings.append(f"生成式对比不可用，已退回逐条原文摘录：{exc}")
                provider = ExtractiveProvider()
                generated = await provider.generate_grounded_answer(prompt, evidence)
                generation_status = "fallback"
        except (LLMConfigurationError, LLMResponseError) as exc:
            warnings.append(f"生成式对比不可用，已退回逐条原文摘录：{exc}")
            provider = ExtractiveProvider()
            generated = await provider.generate_grounded_answer(prompt, evidence)
            generation_status = "fallback"

        if not generated.claims and generation_attempts == 1 and provider.name != "extractive":
            warnings.append("模型首次未给出可核验声明，已对原文证据复核一次。")
            retry_prompt = (
                prompt
                + "\nRecheck the supplied passages once. If each of two papers directly supports "
                "a requested design or setting, return a DIFFERENCE claim citing exact IDs "
                "from both papers. If the requested fact is not in the passages, return no claims."
            )
            try:
                generated = await provider.generate_grounded_answer(retry_prompt, evidence)
            except (LLMConfigurationError, LLMResponseError) as exc:
                warnings.append(f"生成式对比复核失败，已退回逐条原文摘录：{exc}")
                provider = ExtractiveProvider()
                generated = await provider.generate_grounded_answer(prompt, evidence)
                generation_status = "fallback"

        allowed = {item.evidence_id: item for item in evidence}
        claims: list[ComparisonClaimRead] = []
        rejection_reasons: list[str] = []
        for generated_claim in generated.claims:
            requested_evidence_ids = list(dict.fromkeys(generated_claim.evidence_ids))
            evidence_ids = [item for item in requested_evidence_ids if item in allowed]
            if not generated_claim.text.strip():
                rejection_reasons.append("empty_claim")
                continue
            if not evidence_ids or len(evidence_ids) != len(requested_evidence_ids):
                rejection_reasons.append("invalid_or_missing_evidence_id")
                continue
            document_ids = list(dict.fromkeys(allowed[item].document_id for item in evidence_ids))
            relation, dimension, text = _claim_metadata(
                generated_claim.text,
                len(document_ids),
                [allowed[item] for item in evidence_ids],
            )
            if relation in {"agreement", "difference", "conflict"} and len(document_ids) < 2:
                warnings.append(f"声明“{text[:60]}”缺少第二篇论文证据，已降级为单来源声明。")
                relation = "single_source"
            unsupported_facts = _unsupported_numeric_facts(
                text, [allowed[item] for item in evidence_ids]
            )
            if unsupported_facts:
                rejection_reasons.append("unsupported_numeric_fact")
                warnings.append(
                    "一条声明包含原文证据中不存在的数值，已从公开结果中移除："
                    + "、".join(unsupported_facts[:4])
                )
                continue
            claims.append(
                ComparisonClaimRead(
                    text=text,
                    dimension=dimension,
                    relation=relation,
                    evidence_ids=evidence_ids,
                    document_ids=document_ids,
                )
            )

        trace.append(
            AgentTraceStep(
                skill="llm.compare_documents",
                status=generation_status,
                summary=f"使用 {provider.name} 生成跨论文证据对比并校验引用来源。",
                duration_ms=_elapsed_ms(generation_started),
            )
        )
        counts = Counter(item.relation for item in claims)
        cross_document_claim_count = sum(len(item.document_ids) >= 2 for item in claims)
        cited_ids = {item for claim in claims for item in claim.evidence_ids}
        referenced_document_ids = {
            document_id for claim in claims for document_id in claim.document_ids
        }
        generated_count = len(generated.claims)
        audit = ComparisonAuditRead(
            generated_claim_count=generated_count,
            accepted_claim_count=len(claims),
            rejected_claim_count=max(0, generated_count - len(claims)),
            published_claim_citation_coverage=1.0 if claims else 0.0,
            evidence_utilization=round(len(cited_ids) / len(evidence), 4) if evidence else 0.0,
            referenced_document_count=len(referenced_document_ids),
            cross_document_claim_count=cross_document_claim_count,
            rejection_reasons=list(dict.fromkeys(rejection_reasons)),
        )
        answer = _render_validated_answer(question, claims)
        if not claims:
            answer = "模型没有返回带有效原文引用的跨论文声明，当前结果不足以回答该问题。"
            warnings.append("生成结果未通过 Evidence ID 白名单校验，已隐藏未验证回答。")
        elif not cross_document_claim_count:
            warnings.append("声明均为单篇论文事实，尚未形成由多篇原文共同支持的对比结论。")
        return CrossPaperComparisonRead(
            question=question,
            answer=answer,
            papers=papers,
            claims=claims,
            matrix=_build_matrix(claims, papers, allowed),
            evidence=evidence,
            stats=ComparisonStatsRead(
                agreement_count=counts["agreement"],
                difference_count=counts["difference"],
                conflict_count=counts["conflict"],
                single_source_count=counts["single_source"],
                unclassified_count=counts["unclassified"],
                supported_document_count=supported_count,
            ),
            audit=audit,
            insufficient_evidence=not claims or not cross_document_claim_count,
            provider=provider.name,
            model=provider.model,
            trace=trace,
            warnings=warnings,
        )

    def _provider(self, warnings: list[str]) -> LLMProvider:
        if self.provider is not None:
            return self.provider
        try:
            return get_llm_provider()
        except LLMConfigurationError as exc:
            warnings.append(f"LLM 配置不可用，已使用抽取式结果：{exc}")
            return ExtractiveProvider()


def _comparison_prompt(question: str, papers: list[ComparisonPaperRead]) -> str:
    paper_list = "\n".join(f"- {item.title} (document_id={item.document_id})" for item in papers)
    return (
        "Cross-paper comparison task. Answer in the language of the user's question.\n"
        f"User question: {question}\n\n"
        f"Documents in scope:\n{paper_list}\n\n"
        "For every returned claim, start its claim text with a relation marker followed by a "
        "short dimension marker, for example [DIFFERENCE][DIMENSION: context window]. The relation "
        "marker must be exactly one of [AGREEMENT], [DIFFERENCE], [CONFLICT], or [SINGLE_SOURCE]. "
        "AGREEMENT means two or more papers explicitly support the same comparable point. "
        "DIFFERENCE means their designs, settings, results, or scope differ without being "
        "logically incompatible. CONFLICT requires explicit incompatible statements under "
        "comparable conditions; absence of a fact in one paper is never a conflict. SINGLE_SOURCE "
        "is a useful "
        "fact supported by only one paper. Agreement, difference, and conflict claims must cite "
        "evidence from at least two distinct documents. Do not guess missing values or resolve a "
        "conflict without evidence. Absence from the selected passages does not prove a paper "
        "does not report or use something; describe only what the cited passage establishes. "
        "A paper's Related Work or References section describes prior work, not necessarily the "
        "paper's own method; never attribute a cited prior method to the paper's authors, and omit "
        "unrequested background material from the final comparison. "
        "Return supported dimensions even when another dimension lacks evidence. Return no claims "
        "only when every requested dimension lacks direct evidence; never treat silence as proof. "
        "Every number, percentage, year, parameter count, context "
        "length, "
        "or metric value in a claim must occur in the cited evidence. For a requested numeric "
        "dimension, report each available paper's explicit value and cite the particular evidence "
        "item containing that value; never cite only a general architecture passage. "
        "When two papers have direct evidence for different designs, first give one explicit "
        "DIFFERENCE claim comparing them with at least one cited evidence item from each paper. "
        "Two separate SINGLE_SOURCE facts do not replace a requested cross-paper conclusion. "
        "Organize the answer by "
        "comparison dimension rather than merely summarizing papers one after another. The final "
        "published answer will be reconstructed only from claims that pass citation validation."
    )


def _claim_metadata(
    text: str,
    document_count: int,
    evidence: list[EvidenceAnchor],
) -> tuple[str, str, str]:
    match = CLAIM_PREFIX_PATTERN.match(text)
    cleaned = CLAIM_PREFIX_PATTERN.sub("", text, count=1).strip()
    if match:
        relation = RELATION_MAP[match.group(1).lower()]
        dimension = (match.group(2) or "").strip() or _evidence_dimension(evidence)
        return relation, dimension, cleaned
    return (
        "unclassified" if document_count >= 2 else "single_source",
        _evidence_dimension(evidence),
        text.strip(),
    )


def _evidence_dimension(evidence: list[EvidenceAnchor]) -> str:
    sections = [
        " ".join((item.section or "").split())
        for item in evidence
        if item.section and len(item.section.strip()) <= 80
    ]
    return Counter(sections).most_common(1)[0][0] if sections else "综合"


def _unsupported_numeric_facts(text: str, evidence: list[EvidenceAnchor]) -> list[str]:
    facts = list(
        dict.fromkeys(_normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(text))
    )
    if not facts:
        return []
    source = " ".join(
        [
            *(item.quote for item in evidence),
            *(item.document_title or "" for item in evidence),
            *(item.section or "" for item in evidence),
        ]
    )
    source_facts = {_normalise_fact(item) for item in NUMERIC_FACT_PATTERN.findall(source)}
    return [fact for fact in facts if fact not in source_facts]


def _normalise_fact(value: str) -> str:
    normalised = " ".join(value.casefold().replace(",", "").split())
    return (
        normalised.replace("thousand", "k")
        .replace("million", "m")
        .replace("billion", "b")
        .replace(" ", "")
    )


def _select_diverse_evidence(evidence: list[EvidenceAnchor], limit: int) -> list[EvidenceAnchor]:
    unique: dict[str, EvidenceAnchor] = {}
    for item in sorted(evidence, key=lambda value: value.score, reverse=True):
        key = item.chunk_id or "|".join(
            [str(item.page_number), *item.block_ids, " ".join(item.quote.casefold().split())]
        )
        unique.setdefault(key, item)

    remaining = list(unique.values())
    selected: list[EvidenceAnchor] = []
    while remaining and len(selected) < limit:
        used_pages = {item.page_number for item in selected}
        used_types = {item.source_type for item in selected}
        used_sections = {item.section for item in selected if item.section}

        def diversity_score(
            item: EvidenceAnchor,
            pages: set[int] = used_pages,
            types: set[str] = used_types,
            sections: set[str] = used_sections,
        ) -> float:
            similarity = max(
                (_quote_similarity(item.quote, prior.quote) for prior in selected),
                default=0.0,
            )
            return (
                item.score
                + (0.08 if item.page_number not in pages else 0.0)
                + (0.06 if item.source_type not in types else 0.0)
                + (0.04 if item.section and item.section not in sections else 0.0)
                - 0.18 * similarity
            )

        best = max(remaining, key=diversity_score)
        selected.append(best)
        remaining.remove(best)
    return selected


def _comparison_facets(question: str) -> list[str]:
    """Split an explicitly enumerated comparison into focused retrieval queries."""
    _, separator, detail = question.replace("：", ":").partition(":")
    if not separator:
        return []
    detail = re.split(r"[.!?。！？]", detail, maxsplit=1)[0]
    facets = [
        item.strip(" ,;，；、")
        for item in re.split(r"[,，;；、]|\b(?:and|or)\b|以及|与", detail, flags=re.I)
    ]
    return list(dict.fromkeys(item for item in facets if len(item) >= 5))[:4]


def _unique_evidence(evidence: Iterable[EvidenceAnchor]) -> list[EvidenceAnchor]:
    unique: dict[str, EvidenceAnchor] = {}
    for item in evidence:
        key = item.chunk_id or "|".join([item.document_id, str(item.page_number), *item.block_ids])
        unique.setdefault(key, item)
    return list(unique.values())


def _select_faceted_evidence(
    facet_candidates: list[list[EvidenceAnchor]],
    candidates: list[EvidenceAnchor],
    limit: int,
) -> list[EvidenceAnchor]:
    if not facet_candidates:
        return _select_diverse_evidence(candidates, limit)
    selected: list[EvidenceAnchor] = []
    selected_ids: set[str] = set()
    for group in facet_candidates:
        for item in group:
            if item.score < settings.rag_min_score:
                continue
            key = item.chunk_id or "|".join(
                [item.document_id, str(item.page_number), *item.block_ids]
            )
            if key not in selected_ids:
                selected.append(item)
                selected_ids.add(key)
                break
        if len(selected) >= limit:
            return selected
    for item in _select_diverse_evidence(candidates, limit):
        key = item.chunk_id or "|".join([item.document_id, str(item.page_number), *item.block_ids])
        if key not in selected_ids:
            selected.append(item)
            selected_ids.add(key)
        if len(selected) >= limit:
            break
    return selected


def _quote_similarity(left: str, right: str) -> float:
    left_terms = set(tokenize(left))
    right_terms = set(tokenize(right))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def _render_validated_answer(question: str, claims: list[ComparisonClaimRead]) -> str:
    if not claims:
        return ""
    chinese = bool(re.search(r"[\u3400-\u9fff]", question))
    labels = {
        "agreement": "一致",
        "difference": "差异",
        "conflict": "冲突",
        "single_source": "单来源",
        "unclassified": "对比",
    }
    if chinese:
        return "\n".join(
            f"{index}. {claim.dimension}（{labels[claim.relation]}）：{claim.text} "
            f"[{' '.join(claim.evidence_ids)}]"
            for index, claim in enumerate(claims, start=1)
        )
    return "\n".join(
        f"{index}. {claim.dimension} ({claim.relation.replace('_', ' ')}): {claim.text} "
        f"[{' '.join(claim.evidence_ids)}]"
        for index, claim in enumerate(claims, start=1)
    )


def _build_matrix(
    claims: list[ComparisonClaimRead],
    papers: list[ComparisonPaperRead],
    evidence: dict[str, EvidenceAnchor],
) -> list[ComparisonMatrixRowRead]:
    rows = []
    for claim in claims:
        cells = []
        for paper in papers:
            anchors = [
                evidence[evidence_id]
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence
                and evidence[evidence_id].document_id == paper.document_id
            ]
            cells.append(
                ComparisonMatrixCellRead(
                    document_id=paper.document_id,
                    title=paper.title,
                    status="cited" if anchors else "not_cited",
                    evidence_ids=[item.evidence_id for item in anchors],
                    summary=_matrix_summary(anchors),
                )
            )
        rows.append(
            ComparisonMatrixRowRead(
                dimension=claim.dimension,
                relation=claim.relation,
                statement=claim.text,
                cells=cells,
            )
        )
    return rows


def _matrix_summary(evidence: list[EvidenceAnchor]) -> str:
    summaries = []
    for item in evidence:
        compact = " ".join(item.quote.split())
        if compact and compact not in summaries:
            summaries.append(compact)
    value = " / ".join(summaries)
    return value if len(value) <= 360 else value[:357].rstrip() + "…"


def _document_title(document: Document) -> str:
    return document.title or document.original_filename


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
