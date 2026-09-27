from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Iterable
from time import perf_counter
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import get_llm_provider
from app.llm.base import (
    GeneratedAnswer,
    GeneratedClaim,
    LLMConfigurationError,
    LLMInvalidCitationError,
    LLMProvider,
    LLMResponseError,
    LLMTransientError,
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
PARAMETER_COUNT_QUESTION_PATTERN = re.compile(
    r"\b(?:parameter counts?|number of parameters)\b|参数量|模型参数", re.I
)
PARAMETER_VALUE_PATTERN = re.compile(
    r"(?<![\w-])\d+(?:[.,]\d+)?\s*(?:[MB]|million|billion|亿|万)\b", re.I
)
PARAMETER_EVIDENCE_PATTERN = re.compile(r"\bparameters?\b|参数", re.I)
TRAINING_SEQUENCE_QUESTION_PATTERN = re.compile(
    r"\btraining sequence(?:s| lengths?)?\b|训练序列(?:长度)?", re.I
)
TRAINING_EVIDENCE_PATTERN = re.compile(r"\b(?:train|trained|training)\b|训练", re.I)
SEQUENCE_EVIDENCE_PATTERN = re.compile(r"\bsequences?\b|序列", re.I)
TOKEN_VALUE_PATTERN = re.compile(r"(?<![\w-])\d+(?:,\d+)?\s*[- ]?\s*tokens?\b", re.I)
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
        evidence_per_document: int = 5,
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
            facet_candidates, strong = _prefer_primary_evidence(question, facet_candidates, strong)
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

        async def generate_with_retry(
            request_prompt: str,
            request_evidence: list[EvidenceAnchor] | None = None,
        ) -> GeneratedAnswer:
            for attempt in range(3):
                try:
                    return await provider.generate_grounded_answer(
                        request_prompt,
                        request_evidence if request_evidence is not None else evidence,
                    )
                except LLMTransientError:
                    if attempt == 2:
                        raise
                    warnings.append(f"模型服务请求短暂失败，正在进行第 {attempt + 1} 次重试。")
                    await asyncio.sleep(0.5 * (attempt + 1))
            raise AssertionError("unreachable")

        try:
            generated = await generate_with_retry(prompt)
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
                generated = await generate_with_retry(retry_prompt)
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
                generated = await generate_with_retry(retry_prompt)
            except (LLMConfigurationError, LLMResponseError) as exc:
                warnings.append(f"生成式对比复核失败，已退回逐条原文摘录：{exc}")
                provider = ExtractiveProvider()
                generated = await provider.generate_grounded_answer(prompt, evidence)
                generation_status = "fallback"

        if provider.name != "extractive":
            missing_parameter_papers = _missing_parameter_count_papers(
                question, papers, generated.claims, evidence
            )
            missing_sequence_papers = _missing_training_sequence_papers(
                question, papers, generated.claims, evidence, documents=documents
            )
            if missing_parameter_papers or missing_sequence_papers:
                audit_started = perf_counter()
                parameter_ids = {paper.document_id for paper in missing_parameter_papers}
                sequence_ids = {paper.document_id for paper in missing_sequence_papers}
                target_evidence = [
                    item
                    for item in evidence
                    if (item.document_id in parameter_ids and _has_parameter_value(item.quote))
                    or (
                        item.document_id in sequence_ids
                        and _has_training_sequence_value(item.quote)
                    )
                ]
                target_evidence_ids = {item.evidence_id for item in target_evidence}
                target_descriptions = [
                    *(f"{paper.title}: parameter count" for paper in missing_parameter_papers),
                    *(
                        f"{paper.title}: training sequence length"
                        for paper in missing_sequence_papers
                    ),
                ]
                audit_prompt = (
                    "Complete only these missing requested numeric facts: "
                    f"{'; '.join(target_descriptions)}. Original question: {question}\n"
                    "The prior answer omitted explicit values in the supplied passages. "
                    "Return only new claims containing the exact source-stated parameter count or "
                    "training sequence length, with the corresponding evidence IDs. "
                    "Do not equate training sequence length with context window. "
                    "Do not infer or convert values or repeat unrelated facts. "
                    "If no value is stated, return no claims."
                )
                try:
                    supplemental = await generate_with_retry(audit_prompt, target_evidence)
                except (LLMConfigurationError, LLMResponseError) as exc:
                    warnings.append(f"数值事实完整性复核未完成，保留已核验声明：{exc}")
                    audit_status = "failed"
                else:
                    existing_keys = {
                        (claim.text.strip().casefold(), tuple(claim.evidence_ids))
                        for claim in generated.claims
                    }
                    additions = [
                        claim
                        for claim in supplemental.claims
                        if (
                            PARAMETER_VALUE_PATTERN.search(claim.text)
                            or TOKEN_VALUE_PATTERN.search(claim.text)
                        )
                        and any(item in target_evidence_ids for item in claim.evidence_ids)
                        and (claim.text.strip().casefold(), tuple(claim.evidence_ids))
                        not in existing_keys
                    ]
                    generated = GeneratedAnswer(
                        answer=generated.answer,
                        claims=[*generated.claims, *additions],
                    )
                    audit_status = "ok" if additions else "insufficient"
                    warnings.append(
                        f"数值事实完整性复核：针对 {len(target_descriptions)} 项有原文数值的要求"
                        f"补充 {len(additions)} 条候选声明，仍需引用校验。"
                    )
                trace.append(
                    AgentTraceStep(
                        skill="llm.audit_numeric_completeness",
                        status=audit_status,
                        summary="对已检索到但首轮回答未覆盖的数值事实做定向复核。",
                        duration_ms=_elapsed_ms(audit_started),
                    )
                )

        allowed = {item.evidence_id: item for item in evidence}
        claims: list[ComparisonClaimRead] = []
        rejection_reasons: list[str] = []
        explicit_dimensions: list[str] = []
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
            marker = CLAIM_PREFIX_PATTERN.match(generated_claim.text)
            if marker and marker.group(2) and dimension not in explicit_dimensions:
                explicit_dimensions.append(dimension)

        parallel = None
        if len(papers) == 2 and not any(len(item.document_ids) >= 2 for item in claims):
            parallel = _compose_parallel_claim(claims, papers, explicit_dimensions)
            if parallel is not None:
                claims.append(parallel)
                warnings.append("已将同维度的两篇原文声明并列成双来源对照，未添加新事实。")

        if _exact_emissions_measurement_missing(question, papers, evidence):
            claims = []
            parallel = None
            rejection_reasons.append("requested_measurement_not_supported")
            warnings.append("至少一篇论文的入选原文缺少请求的精确 kg CO₂e 测量值，已拒绝数值对比。")

        still_missing_parameters = _missing_parameter_count_papers(
            question, papers, claims, evidence
        )
        still_missing_sequences = _missing_training_sequence_papers(
            question, papers, claims, evidence, documents=documents
        )
        if still_missing_parameters or still_missing_sequences:
            warnings.append(
                "以下请求虽有原文数值，但回答未能给出通过引用校验的结果："
                + "、".join(
                    [
                        *(f"{paper.title} 参数量" for paper in still_missing_parameters),
                        *(f"{paper.title} 训练序列长度" for paper in still_missing_sequences),
                    ]
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
        derived_count = int(parallel is not None)
        audit = ComparisonAuditRead(
            generated_claim_count=generated_count,
            accepted_claim_count=len(claims) - derived_count,
            rejected_claim_count=generated_count - (len(claims) - derived_count),
            derived_claim_count=derived_count,
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
            insufficient_evidence=bool(still_missing_parameters or still_missing_sequences)
            or not claims
            or not cross_document_claim_count,
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


def _has_parameter_value(quote: str) -> bool:
    return bool(PARAMETER_EVIDENCE_PATTERN.search(quote) and PARAMETER_VALUE_PATTERN.search(quote))


def _has_training_sequence_value(quote: str) -> bool:
    return bool(
        TRAINING_EVIDENCE_PATTERN.search(quote)
        and SEQUENCE_EVIDENCE_PATTERN.search(quote)
        and TOKEN_VALUE_PATTERN.search(quote)
    )


def _missing_parameter_count_papers(
    question: str,
    papers: list[ComparisonPaperRead],
    claims: Iterable[GeneratedClaim | ComparisonClaimRead],
    evidence: list[EvidenceAnchor],
) -> list[ComparisonPaperRead]:
    """Find requested parameter counts present in source passages but absent from claims."""

    return _missing_requested_numeric_papers(
        question,
        papers,
        claims,
        evidence,
        question_pattern=PARAMETER_COUNT_QUESTION_PATTERN,
        value_pattern=PARAMETER_VALUE_PATTERN,
        source_patterns=(PARAMETER_EVIDENCE_PATTERN,),
    )


def _missing_training_sequence_papers(
    question: str,
    papers: list[ComparisonPaperRead],
    claims: Iterable[GeneratedClaim | ComparisonClaimRead],
    evidence: list[EvidenceAnchor],
    *,
    documents: list[Document] | None = None,
) -> list[ComparisonPaperRead]:
    """Require a cited token count when a training sequence length is requested."""

    return _missing_requested_numeric_papers(
        question,
        papers,
        claims,
        evidence,
        question_pattern=TRAINING_SEQUENCE_QUESTION_PATTERN,
        value_pattern=TOKEN_VALUE_PATTERN,
        source_patterns=(TRAINING_EVIDENCE_PATTERN, SEQUENCE_EVIDENCE_PATTERN),
        document_scope=_training_sequence_scope(question, documents),
    )


def _training_sequence_scope(question: str, documents: list[Document] | None) -> set[str] | None:
    if not documents:
        return None
    match = re.search(
        r"\b([A-Za-z][A-Za-z0-9_-]{1,24})['’]s\s+(?:explicit\s+)?training sequence",
        question,
        re.I,
    )
    if not match:
        return None
    label = re.sub(r"[^a-z0-9]", "", match.group(1).casefold())
    matched = {
        document.id
        for document in documents
        if label
        in re.sub(
            r"[^a-z0-9]",
            "",
            f"{document.original_filename} {document.title or ''}".casefold(),
        )
    }
    return matched or None


def _normalise_numeric_value(value: str) -> str:
    return _normalise_fact(value.replace("-", " ")).replace("tokens", "token")


def _missing_requested_numeric_papers(
    question: str,
    papers: list[ComparisonPaperRead],
    claims: Iterable[GeneratedClaim | ComparisonClaimRead],
    evidence: list[EvidenceAnchor],
    *,
    question_pattern: re.Pattern[str],
    value_pattern: re.Pattern[str],
    source_patterns: tuple[re.Pattern[str], ...],
    document_scope: set[str] | None = None,
) -> list[ComparisonPaperRead]:
    if not question_pattern.search(question):
        return []
    allowed = {item.evidence_id: item for item in evidence}
    claims_list = list(claims)
    missing: list[ComparisonPaperRead] = []
    for paper in papers:
        if document_scope is not None and paper.document_id not in document_scope:
            continue
        value_sources = [
            item
            for item in evidence
            if item.document_id == paper.document_id
            and all(pattern.search(item.quote) for pattern in source_patterns)
            and value_pattern.search(item.quote)
        ]
        if not value_sources:
            continue
        source_ids = {item.evidence_id for item in value_sources}
        source_values = {
            _normalise_numeric_value(value)
            for item in value_sources
            for value in value_pattern.findall(item.quote)
        }
        covered = False
        for claim in claims_list:
            claim_values = {
                _normalise_numeric_value(value) for value in value_pattern.findall(claim.text)
            }
            if not claim_values.intersection(source_values):
                continue
            if not any(item in source_ids for item in claim.evidence_ids):
                continue
            cited = [allowed.get(item) for item in claim.evidence_ids]
            if not cited or any(item is None for item in cited):
                continue
            if not _unsupported_numeric_facts(claim.text, cited):
                covered = True
                break
        if not covered:
            missing.append(paper)
    return missing


def _exact_emissions_measurement_missing(
    question: str,
    papers: list[ComparisonPaperRead],
    evidence: list[EvidenceAnchor],
) -> bool:
    """Require a measured value from each source for exact emissions comparisons."""

    if not (
        re.search(r"\bexact\b|精确|准确|具体", question, re.I)
        and re.search(r"CO\s*[₂2]\s*(?:e|equivalent)|二氧化碳当量", question, re.I)
        and re.search(r"\b(?:kg|kilograms?)\b|公斤|千克", question, re.I)
    ):
        return False
    metric = r"(?:CO\s*[₂2]\s*(?:e|equivalent)|二氧化碳当量)"
    unit = r"(?:kg|kilograms?|公斤|千克)"
    number = r"\d+(?:[.,]\d+)?"
    measured = re.compile(
        rf"(?:{number}.{{0,32}}{unit}.{{0,32}}{metric}|"
        rf"{number}.{{0,32}}{metric}.{{0,32}}{unit})",
        re.I | re.S,
    )
    return any(
        not any(
            item.document_id == paper.document_id and measured.search(item.quote)
            for item in evidence
        )
        for paper in papers
    )


def _compose_parallel_claim(
    claims: list[ComparisonClaimRead],
    papers: list[ComparisonPaperRead],
    explicit_dimensions: list[str],
) -> ComparisonClaimRead | None:
    """Juxtapose two independently validated facts without inferring a relation."""

    if len(papers) != 2:
        return None
    first_id, second_id = papers[0].document_id, papers[1].document_id
    for dimension in explicit_dimensions:
        family = _dimension_family(dimension)
        first = next(
            (
                item
                for item in claims
                if _dimension_family(item.dimension) == family and item.document_ids == [first_id]
            ),
            None,
        )
        second = next(
            (
                item
                for item in claims
                if _dimension_family(item.dimension) == family and item.document_ids == [second_id]
            ),
            None,
        )
        if first is None or second is None:
            continue
        return ComparisonClaimRead(
            text=f"{first.text.rstrip('。.!')}；{second.text.rstrip('。.!')}。",
            dimension=first.dimension if first.dimension == second.dimension else "模型结构",
            relation="unclassified",
            evidence_ids=list(dict.fromkeys([*first.evidence_ids, *second.evidence_ids])),
            document_ids=[first_id, second_id],
        )
    return None


def _dimension_family(dimension: str) -> str:
    normalized = " ".join(dimension.casefold().split())
    if re.search(
        r"\b(?:architecture|encoder|decoder|model structure)\b|架构|编码器|解码器",
        normalized,
    ):
        return "model architecture"
    return normalized


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
    return list(dict.fromkeys(item for item in facets if len(item) >= 5))[:6]


def _prefer_primary_evidence(
    question: str,
    facet_candidates: list[list[EvidenceAnchor]],
    candidates: list[EvidenceAnchor],
) -> tuple[list[list[EvidenceAnchor]], list[EvidenceAnchor]]:
    """Keep background citations from crowding out a paper's own method evidence."""

    if re.search(
        r"\b(?:related work|references|bibliography|prior work)\b"
        r"|相关工作|参考文献",
        question,
        flags=re.I,
    ):
        return facet_candidates, candidates

    def is_primary(item: EvidenceAnchor) -> bool:
        section = (item.section or "").casefold()
        return not any(
            marker in section
            for marker in ("related work", "references", "bibliography", "相关工作", "参考文献")
        )

    primary = [item for item in candidates if is_primary(item)]
    if not primary:
        return facet_candidates, candidates
    return [[item for item in group if is_primary(item)] for group in facet_candidates], primary


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
