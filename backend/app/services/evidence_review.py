import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import zip_longest
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.future_work import FUTURE_TITLE, SENTENCE_SPLIT
from app.core.future_work import is_future_plan as _future_sentence
from app.llm import get_llm_provider
from app.llm.base import LLMConfigurationError, LLMProvider, LLMResponseError
from app.llm.extractive import ExtractiveProvider
from app.llm.review_limits import (
    THEMATIC_REVIEW_PREFIX,
    filter_review_publication,
)
from app.models.document import Document
from app.models.future_work import FutureWorkDecision
from app.models.paper_source import PaperSource
from app.schemas.agent import EvidenceAnchor
from app.schemas.review import (
    FutureDirectionRead,
    ResearchReviewRead,
    ReviewEvidenceRead,
    ReviewPaperRead,
    SemanticReviewRead,
)
from app.services.citation_graph import CitationGraphService
from app.services.document_content import read_parsed_document
from app.services.future_progress import read_assessment
from app.services.future_work_review import decision_read, direction_key
from app.services.grounded_text import (
    audit_grounded_claims,
    grounding_rejection_warnings,
    render_checked_claims,
)
from app.services.publication_metadata import resolve_publication_year
from app.services.review_context import anchored_citation_relations, expand_review_context
from app.services.review_themes import published_review_themes

FUTURE_HEADING = re.compile(
    r"(?:future\s+(?:work|research|direction)|limitations?|open\s+(?:questions?|problems?)|"
    r"outlook|展望|未来(?:工作|研究|方向)|局限|开放问题)",
    re.IGNORECASE,
)
OVERVIEW_HEADING = re.compile(
    r"^(?:abstract|摘要|introduction|引言|conclusions?|结论|discussion|讨论)",
    re.IGNORECASE,
)
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


def _publication_details(document: Document, source: PaperSource | None, parsed):
    metadata = {}
    if source:
        try:
            metadata = json.loads(source.metadata_json)
        except (TypeError, ValueError):
            metadata = {}
    return resolve_publication_year(document.arxiv_id, metadata, parsed)


def _publication_year(document: Document, source: PaperSource | None) -> int | None:
    # Future-progress validation uses this same dated-source policy as the timeline.
    year, _ = _publication_details(document, source, None)
    if year is not None:
        return year
    return _publication_details(document, source, read_parsed_document(document.id))[0]


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
    reference_blocks = {
        block_id
        for reference in parsed.get("references", [])
        for block_id in reference.get("block_ids", [])
    }
    in_references = False
    mixed_future_section = False
    normalized_title = re.sub(r"\W+", "", unicodedata.normalize("NFKC", title).casefold())

    for page in parsed.get("pages", []):
        page_number = max(1, int(page.get("page_number") or 1))
        for block in page.get("blocks", []):
            text = " ".join(str(block.get("text") or "").split())
            if not text:
                continue
            if (re.sub(r"\W+", "", unicodedata.normalize("NFKC", text).casefold())
                    == normalized_title):
                # Running paper titles can inherit the preceding page's section.
                continue
            if block.get("block_id") in reference_blocks:
                continue
            # A contents entry is not a limitation or future-work paragraph.
            if re.fullmatch(r"\d*(?:\.\d+)*\s*(?:limitations?|future\s+work)\s+\d+", text, re.I):
                continue
            if block.get("type") == "heading":
                current_section = text[:500]
                in_references = bool(
                    re.fullmatch(r"(?:\d+[.\s]*)?(?:references?|bibliography|参考文献)", text, re.I)
                )
                if FUTURE_HEADING.search(text):
                    current_kind = "future_work"
                elif OVERVIEW_HEADING.search(re.sub(r"^[\W\d_]+", "", text)):
                    current_kind = "overview"
                else:
                    current_kind = None
                mixed_future_section = current_kind == "future_work" and bool(re.search(
                    r"conclusions?|discussion|总结|结论|讨论", text, re.I,
                ))
                continue
            if in_references:
                continue
            if block.get("type", "text") not in {"text", "paragraph"}:
                continue
            if FUTURE_TITLE.fullmatch(text):
                continue  # Some PDFs store a section title as a body-text block.
            explicit_future = any(_future_sentence(s) for s in SENTENCE_SPLIT.split(text))
            if (re.search(r"\bwe hope to answer\b", text, re.I)
                    and re.search(r"\bwe (?:(?:only|also) )?(?:present|report)\b|\bour results\b",
                                  text, re.I)
                    and not re.search(r"\bfuture\b|\bwe plan\b", text, re.I)):
                # A question immediately answered by this paragraph's results
                # is study narration, not an unresolved future research plan.
                explicit_future = False
            kind = "future_work" if explicit_future else current_kind
            if kind == "future_work" and mixed_future_section and not explicit_future:
                continue  # A completed result in a combined conclusion is not a plan.
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
                    quote=_bounded_direction_quote(text) if kind == "future_work" else text[:1600],
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
                if (block.get("type", "text") not in {"text", "paragraph"}
                        or len(text) < 80 or "@" in text):
                    continue
                overview.append(
                    _EvidenceDraft(
                        document_id=document.id,
                        document_title=title,
                        publication_year=publication_year,
                        page_number=max(1, int(page.get("page_number") or 1)),
                        block_ids=[str(block.get("block_id"))] if block.get("block_id") else [],
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


def _bounded_direction_quote(text: str) -> str:
    """Keep a late explicit direction inside a literal substring, not a lost prefix."""
    if len(text) <= 1600:
        return text
    sentence = next(
        (item for item in SENTENCE_SPLIT.split(text) if _future_sentence(item)), None
    )
    if sentence is None:
        return text[:1600]
    start = text.index(sentence)
    return text[start : start + 1600]


def _direction_sentence(quote: str) -> str:
    sentences = [item.strip() for item in SENTENCE_SPLIT.split(quote) if item.strip()]
    selected = next((item for item in sentences if _future_sentence(item)), quote.strip())
    return selected[:600]


def _balanced_drafts(drafts: list[_EvidenceDraft], limit: int) -> list[_EvidenceDraft]:
    """Reserve coverage before spending the budget on one paper's extra excerpts."""
    grouped: dict[str, list[_EvidenceDraft]] = {}
    for draft in drafts:
        grouped.setdefault(draft.document_id, []).append(draft)
    # Preserve existing single-paper ordering; for multi-paper reviews reserve one
    # overview, then one future-work passage before supplementary excerpts.
    if len(grouped) <= 1:
        return drafts[:limit]
    ordered = []
    for items in grouped.values():
        overview = [d for d in items if d.kind == "overview"]
        future = [d for d in items if d.kind == "future_work"]
        ordered.append([*overview[:1], *future[:1], *overview[1:], *future[1:]])
    return [d for layer in zip_longest(*ordered) for d in layer if d is not None][:limit]


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
        parsed_documents: dict[str, dict] = {}
        years: dict[str, int | None] = {}
        metadata_anchors: dict[str, EvidenceAnchor] = {}
        warnings = list(graph.warnings)
        for document in documents:
            parsed = read_parsed_document(document.id)
            year, imprint = _publication_details(document, sources.get(document.id), parsed)
            years[document.id] = year
            if imprint:
                metadata_anchors[document.id] = EvidenceAnchor(
                    evidence_id=f"M-{document.id}", document_id=document.id,
                    document_title=document.title, page_number=imprint["page_number"],
                    block_ids=[imprint["block_id"]], bbox=imprint["bbox"],
                    quote=imprint["quote"], score=1,
                )
            if parsed is None:
                continue
            parsed_documents[document.id] = parsed
            drafts.extend(_extract_drafts(document, parsed, years[document.id]))
        graph_evidence, citation_relations = anchored_citation_relations(
            graph, parsed_documents, limit=min(4, max(0, max_evidence - len(documents)))
        )
        available_count = len(drafts)
        future_work_evidence = [
            ReviewEvidenceRead(
                evidence_id=f"F{index}",
                document_id=item.document_id,
                document_title=item.document_title,
                page_number=item.page_number,
                block_ids=item.block_ids,
                bbox=item.bbox,
                section=item.section,
                source_type="text",
                retrieval_mode="lexical",
                quote=item.quote,
                score=1.0,
                evidence_kind="future_work",
            )
            for index, item in enumerate(
                (draft for draft in drafts if draft.kind == "future_work"), start=1
            )
        ]
        # Local progress lookup must not depend on how many overview excerpts
        # fit into the generation prompt, either.
        future_work_evidence.extend(
            ReviewEvidenceRead(
                evidence_id=f"L{index}",
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
                evidence_kind="later_progress",
            )
            for index, item in enumerate(
                (draft for draft in drafts if draft.kind == "overview"), start=1
            )
        )
        body_budget = max_evidence - len(graph_evidence)
        drafts = _balanced_drafts(drafts, body_budget)
        covered = {draft.document_id for draft in drafts}
        missing_titles = [
            document.title or document.original_filename
            for document in documents
            if document.id not in covered
        ]
        if missing_titles:
            warnings.append(
                "以下所选论文未获得综述证据，不能据此宣称覆盖全部文献：" + "、".join(missing_titles)
            )
        if available_count > len(drafts):
            warnings.append(
                f"正文证据预算为 {body_budget}，已按文献均衡选择 {len(drafts)} 条；"
                f"另有 {available_count - len(drafts)} 条候选未发送。"
            )
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
        evidence.extend(graph_evidence)
        evidence, context_chars = expand_review_context(evidence, parsed_documents)
        if context_chars:
            warnings.append(
                f"综述摘录补入 {context_chars} 字符的同页相邻完整正文；"
                "保留各原块位置，不跨标题或页码。单条最多2400字符、总追加最多8000字符；"
                "证据条数预算及独立Future Work池不变。"
            )
        if graph.edges:
            warnings.append(
                f"综述生成仅获得 {len(citation_relations)} 条有原页锚点的明确书目关联；"
                "未发送的图谱边不作为生成依据。书目匹配不证明贡献继承或科研影响。"
            )
        evidence_by_document: dict[str, list[ReviewEvidenceRead]] = {}
        for item in evidence:
            evidence_by_document.setdefault(item.document_id, []).append(item)
        overview_by_document: dict[str, list[ReviewEvidenceRead]] = {}
        for item in future_work_evidence:
            if item.evidence_kind == "later_progress":
                overview_by_document.setdefault(item.document_id, []).append(item)
        papers = [
            ReviewPaperRead(
                document_id=document.id,
                title=graph_nodes[document.id].title,
                publication_year=years[document.id],
                metadata_evidence=metadata_anchors.get(document.id),
                graph_role=graph_nodes[document.id].role,
                foundation_score=graph_nodes[document.id].foundation_score,
                overview_evidence_ids=[
                    item.evidence_id
                    for item in evidence_by_document.get(document.id, [])
                    if item.evidence_kind == "overview"
                ],
                future_work_evidence_ids=[
                    item.evidence_id
                    for item in future_work_evidence
                    if item.document_id == document.id and item.evidence_kind == "future_work"
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
        graph_edges = {(item.source_document_id, item.target_document_id) for item in graph.edges}
        directions: list[FutureDirectionRead] = []
        for item in future_work_evidence:
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
                    direction_id=direction_key(
                        item.document_id, item.page_number, item.block_ids, item.quote
                    ),
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
        for direction in directions:
            try:
                direction.assessment = read_assessment(self.session, direction.direction_id)
                if direction.assessment and direction.assessment.excluded_from_recommendations:
                    direction.status = "evidence_addressed"
                    direction.exploration_score = 0
                    direction.reason = (
                        "所选更晚原文支持全部明确目标，且独立排除校审通过；"
                        "不再作为开放方向推荐。"
                    )
            except (ValueError, LLMResponseError):
                warnings.append("保存的后续进展来源已变更，请重新核对；未采用旧结论。")
            saved = self.session.get(FutureWorkDecision, direction.direction_id)
            if saved is None:
                continue
            try:
                direction.confirmation = decision_read(self.session, saved)
            except ValueError:
                warnings.append("已保存的方向核验证据已变更或移除，请重新核验。")
                continue
            if saved.decision == "confirmed_addressed":
                direction.status = "confirmed_addressed"
                direction.exploration_score = 0
            elif saved.decision == "open":
                direction.status = "open"
                direction.exploration_score = 0.55
            elif saved.decision == "needs_review":
                direction.status = "possibly_addressed"
            direction.reason = f"人工核验记录：{saved.reason}"
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
        question = THEMATIC_REVIEW_PREFIX + (
            "请基于给定多篇论文证据生成简短中文领域综述。按研究主题综合，不逐篇罗列摘要。"
            "优先回答共同问题、不同方法选择及各自约束，不用两篇事实的简单拼接充当综合。"
            "分别处理不同约束，不等于已验证两种方法可组合、效果互补或共同支持更大规模。"
            "只有实际引用的原文明确支持联合方法与效果时才报告联合结论，否则只比较各自机制。"
            "只在各篇正文均有依据时比较，不为主题或条目数量硬凑结论。"
            "以机制为主，必要的数值必须保留实验对象、比较基线与适用条件，否则省略数值。"
            "必须区分某项研究中的设置与方法的普遍限制；不能把本文只研究某种设置写成方法"
            "只能支持该设置。内存容量、读写复杂度和浮点计算复杂度是不同约束，"
            "不能由IO优化动机推断计算复杂度的成因或降低。"
            "不要用模糊的短、长或大代替相对变量条件，不因压缩篇幅删除必要实验条件。"
            "理论梯度结论须保留初始化阶段、参数所在位置和期望量，不概括为全程的所有梯度。"
            "不同论文仅在各自明确研究的共同问题上比较，不把早期通用方法归因到后来才研究的"
            "特定训练技巧。正文使用可读的方法或论文名称，P编号仅用于来源身份判别。"
            "局域引用关系只能依据下列关联及对应G类书目，不能据此推断方法继承、"
            "因果影响、学界地位或方向已解决；没有书目依据时不生成引用关系。"
            "Future Work的状态由系统单独提供，当前未发现不等于未被解决。"
            "下列future_work角色只是来源阅读提示；仍须核对摘录本身。作者计划不能改写成"
            "已经实现的功能，旧文计划也不代表今天仍未解决。\n"
            "摘录角色提示（组织信息，不是额外事实；不替代原文）："
            + json.dumps(
                {item.evidence_id: item.evidence_kind for item in evidence}, ensure_ascii=False
            )
            + "\n"
            "本地原页书目关联（不是额外研究结果）："
            + json.dumps(
                [relation.model_dump() for relation in citation_relations], ensure_ascii=False
            )
        )
        try:
            generated = await provider.generate_grounded_answer(question, grounding)
        except (LLMConfigurationError, LLMResponseError) as exc:
            warnings.append(f"生成式综述不可用，已使用抽取式综述：{exc}")
            provider = ExtractiveProvider()
            generated = await provider.generate_grounded_answer(question, grounding)
        claims, grounding_audit = audit_grounded_claims(generated.claims, grounding)
        for key, reason in generated.field_rejections.items():
            warnings.append(f"主题条目 {key} 暂缓：{reason}")
        warnings.extend(grounding_rejection_warnings(grounding_audit))
        claims, publication_warnings = filter_review_publication(claims, grounding)
        warnings.extend(publication_warnings)
        semantic_review = None
        if claims and callable(getattr(provider, "review_claims", None)):
            input_count = len(claims)
            try:
                verdicts = await provider.review_claims(claims, grounding)
                # Validate even custom providers, not only the HTTP implementation.
                from app.llm.claim_review import validate_verdicts

                verdicts = validate_verdicts({"verdicts": verdicts}, input_count)
                claims = [claims[v["claim_index"]] for v in verdicts if v["status"] == "supported"]
                semantic_review = SemanticReviewRead(
                    status="checked", input_claim_count=input_count,
                    accepted_claim_count=len(claims),
                    verdicts=verdicts,
                )
                for v in verdicts:
                    if v["status"] != "supported":
                        warnings.append(
                            f"独立上下文语义校审暂缓第{v['claim_index'] + 1}条：{v['reason']}")
            except (LLMConfigurationError, LLMResponseError) as exc:
                claims = []
                semantic_review = SemanticReviewRead(status="unavailable",
                    input_claim_count=input_count, accepted_claim_count=0)
                warnings.append(f"语义校审不可用，未发布未校审的综述陈述；未自动重试：{exc}")
        anchors = {item.evidence_id: item for item in grounding}
        multi_source_claims = sum(
            len(
                {
                    anchors[key].document_id
                    for key in claim.evidence_ids
                    if anchors[key].source_type != "reference"
                }
            )
            >= 2
            for claim in claims
        )
        if provider.name != "extractive" and len(documents) > 1 and multi_source_claims < 2:
            warnings.append(
                f"当前仅有 {multi_source_claims} 条包含多篇正文来源的论点，"
                "尚未达到跨文综合目标；不能把多篇摘录汇集当成完整领域综述。"
            )
        return ResearchReviewRead(
            generated_at=datetime.now(UTC),
            review=render_checked_claims(claims),
            claims=claims,
            grounding_audit=grounding_audit,
            semantic_review=semantic_review,
            themes=published_review_themes(generated.claims, claims, grounding),
            papers=papers,
            future_directions=directions,
            evidence=evidence,
            future_work_evidence=future_work_evidence,
            graph_stats=graph.stats,
            citation_relations=citation_relations,
            provider=provider.name,
            model=provider.model,
            insufficient_evidence=not claims,
            warnings=warnings,
        )
