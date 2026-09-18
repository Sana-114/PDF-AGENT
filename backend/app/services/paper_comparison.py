from __future__ import annotations

import re
from collections import Counter
from time import perf_counter
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMProvider, LLMResponseError
from app.llm.extractive import ExtractiveProvider
from app.models.document import Document
from app.schemas.agent import AgentTraceStep, EvidenceAnchor
from app.schemas.comparison import (
    ComparisonClaimRead,
    ComparisonPaperRead,
    ComparisonStatsRead,
    CrossPaperComparisonRead,
)
from app.services.retrieval import HybridRetriever

RELATION_PATTERN = re.compile(
    r"^\s*\[(AGREEMENT|DIFFERENCE|CONFLICT|SINGLE_SOURCE)\]\s*",
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

        for document in documents:
            started = perf_counter()
            candidates = self.retriever.search(
                question,
                document_ids=[document.id],
                top_k=evidence_per_document,
            )
            strong = [item for item in candidates if item.score >= settings.rag_min_score]
            document_evidence: list[EvidenceAnchor] = []
            for item in strong:
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
                    coverage="supported" if document_evidence else "no_evidence",
                )
            )
            trace.append(
                AgentTraceStep(
                    skill="search_evidence",
                    status="ok" if document_evidence else "insufficient",
                    summary=f"《{title}》保留 {len(document_evidence)} 条强证据。",
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
                evidence=evidence,
                stats=ComparisonStatsRead(supported_document_count=supported_count),
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
        try:
            generated = await provider.generate_grounded_answer(prompt, evidence)
        except (LLMConfigurationError, LLMResponseError) as exc:
            warnings.append(f"生成式对比不可用，已退回逐条原文摘录：{exc}")
            provider = ExtractiveProvider()
            generated = await provider.generate_grounded_answer(prompt, evidence)
            generation_status = "fallback"

        allowed = {item.evidence_id: item for item in evidence}
        claims: list[ComparisonClaimRead] = []
        for generated_claim in generated.claims:
            evidence_ids = list(
                dict.fromkeys(
                    item for item in generated_claim.evidence_ids if item in allowed
                )
            )
            if not generated_claim.text.strip() or not evidence_ids:
                continue
            document_ids = list(
                dict.fromkeys(allowed[item].document_id for item in evidence_ids)
            )
            relation, text = _claim_relation(generated_claim.text, len(document_ids))
            if relation in {"agreement", "difference", "conflict"} and len(document_ids) < 2:
                warnings.append(
                    f"声明“{text[:60]}”缺少第二篇论文证据，已降级为单来源声明。"
                )
                relation = "single_source"
            claims.append(
                ComparisonClaimRead(
                    text=text,
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
        answer = generated.answer
        if not claims:
            answer = "模型没有返回带有效原文引用的跨论文声明，当前结果不足以回答该问题。"
            warnings.append("生成结果未通过 Evidence ID 白名单校验，已隐藏未验证回答。")
        return CrossPaperComparisonRead(
            question=question,
            answer=answer,
            papers=papers,
            claims=claims,
            evidence=evidence,
            stats=ComparisonStatsRead(
                agreement_count=counts["agreement"],
                difference_count=counts["difference"],
                conflict_count=counts["conflict"],
                single_source_count=counts["single_source"],
                unclassified_count=counts["unclassified"],
                supported_document_count=supported_count,
            ),
            insufficient_evidence=not claims,
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
    paper_list = "\n".join(
        f"- {item.title} (document_id={item.document_id})" for item in papers
    )
    return (
        "Cross-paper comparison task. Answer in the language of the user's question.\n"
        f"User question: {question}\n\n"
        f"Documents in scope:\n{paper_list}\n\n"
        "For every returned claim, start its claim text with exactly one marker: "
        "[AGREEMENT], [DIFFERENCE], [CONFLICT], or [SINGLE_SOURCE]. "
        "AGREEMENT means two or more papers explicitly support the same comparable point. "
        "DIFFERENCE means their designs, settings, results, or scope differ without being "
        "logically incompatible. CONFLICT requires explicit incompatible statements under "
        "comparable conditions; absence of a fact in one paper is never a conflict. SINGLE_SOURCE "
        "is a useful "
        "fact supported by only one paper. Agreement, difference, and conflict claims must cite "
        "evidence from at least two distinct documents. Do not guess missing values or resolve a "
        "conflict without evidence. Organize the answer by comparison dimension rather than merely "
        "summarizing the papers one after another."
    )


def _claim_relation(text: str, document_count: int) -> tuple[str, str]:
    match = RELATION_PATTERN.match(text)
    cleaned = RELATION_PATTERN.sub("", text, count=1).strip()
    if match:
        return RELATION_MAP[match.group(1).lower()], cleaned
    return ("unclassified" if document_count >= 2 else "single_source"), text.strip()


def _document_title(document: Document) -> str:
    return document.title or document.original_filename


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
