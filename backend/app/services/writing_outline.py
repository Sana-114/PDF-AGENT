import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.harness import ResearchAgent
from app.models.document import Document
from app.models.paper_source import PaperSource
from app.schemas.agent import AnswerClaim, AskRequest, EvidenceAnchor
from app.schemas.writing import (
    VerifiedReferenceRead,
    WritingOutlineRead,
    WritingSectionRead,
)
from app.services.claim_semantics import evidence_scope_note
from app.services.document_content import read_parsed_document
from app.services.grounded_text import grounding_rejection_warnings
from app.services.publication_metadata import resolve_publication_year
from app.services.writing_context import (
    missing_explicit_evaluation_protocol,
    withhold_unqualified_continuation_claims,
)

WRAPPED_TITLE_WORD = re.compile(r"\b([A-Za-z]{3,})-\s+([A-Za-z]{2,})\b")
HYPHENATED_PREFIXES = {
    "cross",
    "few",
    "high",
    "large",
    "low",
    "multi",
    "non",
    "post",
    "pre",
    "self",
    "semi",
    "zero",
}
PROPOSAL_PREMISE = re.compile(
    r"\b(?:this writing idea|the proposed study|our proposed study|the proposed project|"
    r"the user's idea|the research idea)\b|用户(?:想法|idea)|本研究拟|本项目拟|"
    r"本(?:研究|项目)(?:尚未|没有|未开展|未进行)",
    re.I,
)
FIRST_PERSON_FINDING = re.compile(
    r"\bwe\s+(?:found|find|show|demonstrate|achieve|report|observe|propose|train|evaluate)\b|"
    r"\bour\s+(?:results?|experiments?|findings?)\b|我们(?:发现|证明|提出|训练|观察|取得)",
    re.I,
)
UNIVERSAL_PRIOR_WORK = re.compile(
    r"\bno\s+(?:prior|previous|earlier)\s+work\b|"
    r"\b(?:prior|previous|earlier)\s+work\s+(?:had|has|did)\s+not\b",
    re.I,
)
BOUNDED_PRIOR_WORK = re.compile(
    r"\bnone\s+of\s+(?:these|the\s+(?:cited|reviewed))\s+(?:works?|studies)\b", re.I
)


def _overstates_bounded_prior_work(text: str, evidence: Sequence[EvidenceAnchor]) -> bool:
    """Hold an explicit universal novelty claim based on a bounded literature statement.

    This narrow cue check is not a general entailment or novelty verifier.
    """
    return bool(UNIVERSAL_PRIOR_WORK.search(text)) and any(
        BOUNDED_PRIOR_WORK.search(" ".join(item.quote.split())) for item in evidence
    )


def _optimizer_caption_subject_conflict(text: str, evidence: Sequence[EvidenceAnchor]) -> bool:
    """Hold a narrow experiment-subject conflict with a uniquely named table caption.

    No paper/model/optimizer identity is hard-coded; not a general attribution audit.
    """
    subject = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s+pre-training\s+with\b", text)
    if subject is None:
        return False
    models = {
        m.casefold()
        for item in evidence
        for m in re.findall(
            r"Table\s+\d+\s*:\s*Fine-tuning\s+([A-Za-z][A-Za-z0-9_-]*)\s+models\s+"
            r"pre-trained\s+with\b",
            " ".join(item.quote.split()),
            re.I,
        )
    }
    return len(models) == 1 and subject[1].casefold() not in models


def _clean_reference_title(title: str) -> str:
    """Repair a split word in display text while retaining meaningful hyphens."""

    title = unicodedata.normalize("NFKC", title.replace("\u00ad", ""))
    return " ".join(
        WRAPPED_TITLE_WORD.sub(
            lambda match: match.group(1)
            + ("-" if match.group(1).casefold() in HYPHENATED_PREFIXES else "")
            + match.group(2),
            title,
        ).split()
    )


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _source_metadata(source: PaperSource | None) -> dict[str, Any]:
    if source is None:
        return {}
    try:
        parsed = json.loads(source.metadata_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _formatted_reference(
    *,
    title: str,
    authors: list[str],
    publication_year: int | None,
    venue: str | None,
    doi: str | None,
    arxiv_id: str | None,
) -> str:
    parts: list[str] = []
    if authors:
        parts.append(", ".join(authors))
    if publication_year:
        parts.append(f"({publication_year})")
    parts.append(title.rstrip("."))
    if venue:
        parts.append(venue.rstrip("."))
    if doi:
        parts.append(f"doi:{doi}")
    elif arxiv_id:
        parts.append(f"arXiv:{arxiv_id}")
    return ". ".join(parts) + "."


def _reference_records(
    session: Session,
    evidence: Sequence[EvidenceAnchor],
) -> list[VerifiedReferenceRead]:
    evidence_by_document: dict[str, list[str]] = defaultdict(list)
    document_order: list[str] = []
    for item in evidence:
        if item.document_id not in evidence_by_document:
            document_order.append(item.document_id)
        evidence_by_document[item.document_id].append(item.evidence_id)
    if not document_order:
        return []
    documents = {
        item.id: item
        for item in session.scalars(select(Document).where(Document.id.in_(document_order))).all()
    }
    sources = {
        item.document_id: item
        for item in session.scalars(
            select(PaperSource).where(PaperSource.document_id.in_(document_order))
        ).all()
    }
    references: list[VerifiedReferenceRead] = []
    for index, document_id in enumerate(document_order, start=1):
        document = documents.get(document_id)
        if document is None:
            continue
        source = sources.get(document_id)
        metadata = _source_metadata(source)
        parsed = read_parsed_document(document.id) or {}
        title = _clean_reference_title(document.title or document.original_filename)
        authors = (
            _json_list(document.authors_json)
            or [str(item) for item in metadata.get("authors", []) if str(item).strip()]
            or [str(item) for item in parsed.get("authors", []) if str(item).strip()]
        )
        publication_year, imprint = resolve_publication_year(document.arxiv_id, metadata, parsed)
        venue = str(metadata.get("venue") or "").strip() or None
        metadata_evidence = None
        if imprint:
            venue = venue or imprint["venue"]
            metadata_evidence = EvidenceAnchor(
                evidence_id=f"M{index}", document_id=document_id, document_title=title,
                page_number=imprint["page_number"], block_ids=[imprint["block_id"]],
                bbox=imprint["bbox"], quote=imprint["quote"], score=1,
            )
        doi = (source.doi if source else None) or str(metadata.get("doi") or "").strip() or None
        arxiv_id = (
            document.arxiv_id
            or (source.arxiv_id if source else None)
            or str(metadata.get("arxiv_id") or "").strip()
            or None
        )
        landing_url = (source.landing_url if source else None) or (
            str(metadata.get("landing_url") or "").strip() or None
        )
        verified_fields = ["title"]
        for field, value in (
            ("authors", authors),
            ("publication_year", publication_year),
            ("venue", venue),
            ("doi", doi),
            ("arxiv_id", arxiv_id),
        ):
            if value:
                verified_fields.append(field)
        references.append(
            VerifiedReferenceRead(
                citation_key=f"P{index}",
                document_id=document_id,
                title=title,
                authors=authors,
                publication_year=publication_year,
                venue=venue,
                doi=doi,
                arxiv_id=arxiv_id,
                landing_url=landing_url,
                provenance=source.provider if source else "local_pdf",
                verified_fields=verified_fields,
                formatted_citation=_formatted_reference(
                    title=title,
                    authors=authors,
                    publication_year=publication_year,
                    venue=venue,
                    doi=doi,
                    arxiv_id=arxiv_id,
                ),
                evidence_ids=list(dict.fromkeys(evidence_by_document[document_id])),
                metadata_evidence=metadata_evidence,
            )
        )
    return references


def _section_content(
    idea: str,
    references: Sequence[VerifiedReferenceRead],
    evidence: Sequence[EvidenceAnchor],
    claims,
    language: str,
) -> list[WritingSectionRead]:
    evidence_by_document: dict[str, list[EvidenceAnchor]] = defaultdict(list)
    for item in evidence:
        evidence_by_document[item.document_id].append(item)
    supported_claims = [f"- {item.text} [{' '.join(item.evidence_ids)}]" for item in claims[:6]]
    if language == "en":
        abstract_lines = [
            f"This proposed study investigates: {idea}",
            *[
                f"Evidence basis: {item.text} [{' '.join(item.evidence_ids)}]"
                for item in claims[:2]
            ],
        ]
        introduction_lines = [
            f"- Define the research problem and scope around **{idea}**.",
            "- Identify the evidence-supported gap and formulate testable research questions.",
            *supported_claims,
        ]
        related_title = "Related Work"
        titles = ("Abstract", "Introduction", related_title)
    else:
        abstract_lines = [
            f"本研究拟围绕“{idea}”展开。",
            *[f"证据基础：{item.text} [{' '.join(item.evidence_ids)}]" for item in claims[:2]],
        ]
        introduction_lines = [
            f"- 界定“{idea}”对应的研究问题与适用范围。",
            "- 基于已有证据定位研究缺口，并形成可检验的研究问题。",
            *supported_claims,
        ]
        related_title = "相关工作"
        titles = ("摘要", "引言", related_title)
    related_lines: list[str] = []
    related_evidence_ids: list[str] = []
    for reference in references:
        anchor_ids = {item.evidence_id for item in evidence_by_document[reference.document_id]}
        items = [claim for claim in claims if anchor_ids.intersection(claim.evidence_ids)][:2]
        related_evidence_ids.extend(key for claim in items for key in claim.evidence_ids)
        snippets = " ".join(f"{claim.text} [{' '.join(claim.evidence_ids)}]" for claim in items)
        if not snippets:
            snippets = (
                "尚无通过审校的相关工作陈述；原文摘录可在证据面板核对。"
                if language == "zh"
                else "No reviewed related-work claim is available; inspect the original evidence."
            )
        related_lines.append(f"- **[{reference.citation_key}] {reference.title}**：{snippets}")
    if not related_lines:
        related_lines.append(
            "- 当前文献库没有检索到可引用证据，请先补充相关论文。"
            if language == "zh"
            else "- No citable evidence was found; add relevant papers before drafting."
        )
    claim_evidence_ids = list(
        dict.fromkeys(item for claim in claims for item in claim.evidence_ids)
    )
    return [
        WritingSectionRead(
            section_id="abstract",
            title=titles[0],
            content_markdown="\n\n".join(abstract_lines),
            evidence_ids=list(
                dict.fromkeys(item for claim in claims[:2] for item in claim.evidence_ids)
            ),
        ),
        WritingSectionRead(
            section_id="introduction",
            title=titles[1],
            content_markdown="\n".join(introduction_lines),
            evidence_ids=claim_evidence_ids,
        ),
        WritingSectionRead(
            section_id="related_work",
            title=related_title,
            content_markdown="\n".join(related_lines),
            evidence_ids=list(dict.fromkeys(related_evidence_ids)),
        ),
    ]


def _compact_claims(
    claims: Sequence[AnswerClaim], evidence: Sequence[EvidenceAnchor],
    documents: Sequence[Document], budget: int,
) -> tuple[list[AnswerClaim], list[AnswerClaim]]:
    """Fair, deterministic presentation budget; extras remain available, not failed."""
    by_id = {item.evidence_id: item.document_id for item in evidence}
    allowed = {item.id for item in documents}
    sources = [{by_id[key] for key in claim.evidence_ids if key in by_id} & allowed
               for claim in claims]
    selected: set[int] = set()
    counts: dict[str, int] = defaultdict(int)
    for _ in range(2):
        for document in documents:
            if len(selected) >= budget:
                break
            for index, ids in enumerate(sources):
                if (index not in selected and document.id in ids
                        and all(counts[key] < 2 for key in ids)):
                    selected.add(index)
                    for key in ids:
                        counts[key] += 1
                    break
    return (
        [claim for index, claim in enumerate(claims) if index in selected],
        [claim for index, claim in enumerate(claims) if index not in selected],
    )


class WritingOutlineService:
    def __init__(self, session: Session, *, agent: ResearchAgent | None = None) -> None:
        self.session = session
        self.agent = agent or ResearchAgent(session)

    async def generate(
        self,
        idea: str,
        documents: Sequence[Document],
        *,
        top_k: int = 12,
        language: str = "zh",
    ) -> WritingOutlineRead:
        fact_budget = min(6, max(1, 2 * len(documents)))
        question = (
            f"围绕以下研究 idea 提取可用于论文框架的已有事实、研究缺口和相关工作：{idea}。"
            "请使用中文，只依据证据，不要生成或猜测参考文献。"
            "分别提取每篇所选论文相关的架构或方法事实，不把参考文献条目当作本文贡献。"
            "每篇选择一至两条直接相关事实；需要比较时引用各自来源。不要以证据编号开头，"
            f"最多返回 {fact_budget} 条非重复陈述；只选择与 idea 直接相关的事实。"
            "将研究作者比较多个方法写成研究比较，不将被比较方法写成比较它自身的主语。"
            "涉及性能时明确下游任务、微调目标及比较对象；完整保留可能、可以、仅在等限定。"
            "还须保留评估方式，不能混用少样本线性评估与完整微调。"
            "不要仅因检索到就加入未要求的实验、额外模型或旁支数值。"
            "引用必须指向实际陈述该事实的摘录，不能仅因摘录来自同篇论文或同一页就引用。"
            "不复制长段原文，不将摘录缺失当作创新性证明。保留初始尝试、调参及适用范围，"
            "涉及续页条件必须同时引用续页证据。没有新实验，不生成本研究的结果。"
            "只返回原论文事实；不要把研究idea或用户前提列为原论文事实。"
            "引用带有续页context_for的未完结来源时，在该条陈述内也列出对应续页引用。"
            if language == "zh"
            else "Extract evidence-supported facts, research gaps and related work for this "
            f"writing idea: {idea}. Respond in English and use only the supplied evidence. "
            "Do not generate or guess references. Extract relevant architecture or method "
            "facts from each selected paper separately, without attributing bibliography "
            "entries to that paper's contributions."
            " Select one or two directly relevant facts per paper; cite each source in comparisons."
            f" Return at most {fact_budget} non-duplicated claims directly relevant to the idea."
            " Attribute a study's comparison of architectures to the study, not to one model"
            " evaluating itself against itself. For performance comparisons explicitly preserve"
            " the downstream task/fine-tuning target, comparator and experimental scope."
            " Explicitly retain the evaluation protocol; do not conflate few-shot linear"
            " evaluation with full fine-tuning. Do not add unrequested experiments, extra"
            " model variants or incidental numbers merely because evidence mentions them."
            " Preserve modal qualifiers (can, may, under these conditions); do not turn a"
            " conditional result into an unconditional guarantee."
            " Each citation must point to the excerpt stating that fact, not merely the same paper"
            " or page. A methodological overview may use the anchored abstract; it cannot prove"
            " unreported experiments or novelty."
            " Do not start sentences with evidence IDs or copy long passages. Absence from excerpts"
            " is not proof of novelty. Retain initial attempts, tuning and scope; cite continuation"
            " evidence when relevant. No new experiments exist; do not invent study results."
            " Return source-paper facts only, not statements about the writing idea "
            "or user premise."
            " Describe published findings in third person; never speak as the paper's authors."
            " For an unfinished source with adjacent_context_for continuations, include the linked"
            " continuation citation within that same claim, not merely in another claim."
        )
        request = AskRequest(
            question=question,
            document_ids=[item.id for item in documents],
            retrieval_query=idea,
            top_k=top_k,
        )
        grounded = await self.agent.ask(
            request, diversify_documents=True, include_writing_context=True
        )
        premise_count = sum(bool(PROPOSAL_PREMISE.search(claim.text)) for claim in grounded.claims)
        voice_count = sum(
            bool(FIRST_PERSON_FINDING.search(claim.text)) for claim in grounded.claims
        )
        evidence_by_id = {item.evidence_id: item for item in grounded.evidence}
        scope_claims = {
            index
            for index, claim in enumerate(grounded.claims)
            if _overstates_bounded_prior_work(
                claim.text,
                [evidence_by_id[key] for key in claim.evidence_ids if key in evidence_by_id],
            )
        }
        attribution_claims = {
            index
            for index, claim in enumerate(grounded.claims)
            if _optimizer_caption_subject_conflict(
                claim.text,
                [evidence_by_id[key] for key in claim.evidence_ids if key in evidence_by_id],
            )
        }
        protocol_claims = {
            index for index, claim in enumerate(grounded.claims)
            if missing_explicit_evaluation_protocol(
                claim.text,
                [evidence_by_id[key] for key in claim.evidence_ids if key in evidence_by_id],
            )
        }
        limitation_count = sum(evidence_scope_note(claim.text) for claim in grounded.claims)
        source_claims = [
            claim
            for index, claim in enumerate(grounded.claims)
            if not PROPOSAL_PREMISE.search(claim.text)
            and not FIRST_PERSON_FINDING.search(claim.text)
            and index not in scope_claims
            and index not in attribution_claims
            and index not in protocol_claims
            and not evidence_scope_note(claim.text)
        ]
        claims, withheld = withhold_unqualified_continuation_claims(
            source_claims, grounded.evidence
        )
        claims, deferred = _compact_claims(claims, grounded.evidence, documents, fact_budget)
        references = _reference_records(self.session, grounded.evidence)
        sections = _section_content(
            idea,
            references,
            grounded.evidence,
            claims,
            language,
        )
        proposed_title = idea[:120]
        warnings: list[str] = []
        audit = grounded.grounding_audit
        input_count = audit.input_claim_count if audit else len(grounded.claims)
        if input_count > fact_budget:
            warnings.append(
                f"防护输入 {input_count} 条事实，超过本次 {fact_budget} 条简洁预算；"
                "暂缓原因与有据补充事实分别保留，不改写原始输出。"
            )
        paper_counts: dict[str, int] = defaultdict(int)
        if audit:
            paper_counts.update(audit.document_claim_counts)
        else:
            for claim in grounded.claims:
                for document_id in {evidence_by_id[key].document_id for key in claim.evidence_ids
                                    if key in evidence_by_id}:
                    paper_counts[document_id] += 1
        for document in documents:
            count = paper_counts[document.id]
            if count > 2:
                warnings.append(
                    f"文献《{document.title or document.original_filename}》被用于 {count} 条陈述，"
                    "超过每篇两条简洁预算；框架与补充事实分开保留，请审校相关性。"
                )
        if deferred:
            warnings.append(
                f"为保持文献覆盖与简洁性，{len(deferred)} 条有据陈述留在补充事实区，"
                "未纳入框架或复制正文；这不是来源审校失败。"
            )
        if audit:
            warnings.extend(grounding_rejection_warnings(audit))
        if protocol_claims:
            warnings.append(
                f"已暂缓 {len(protocol_claims)} 条遗漏原文明示评估方式的性能陈述；"
                "少样本线性评估不能被表述为完整微调结果，请核对所引实验段落。"
            )
        if premise_count:
            warnings.append(
                f"已移除 {premise_count} 条把研究计划／用户前提写成论文事实的陈述；"
                "研究设想仅在拟议框架中表达，不借用论文出处证明。"
            )
        if voice_count:
            warnings.append(
                f"已暂缓 {voice_count} 条第一人称研究结果陈述；请以第三人称标明原论文归属，"
                "不能将论文作者的结果写成本研究结果。"
            )
        if scope_claims:
            warnings.append(
                f"已暂缓 {len(scope_claims)} 条扩大既有工作范围的陈述；"
                "原文仅涉及部分已引用工作，不能据此声称所有先前研究都未涉及该方法。"
            )
        if attribution_claims:
            warnings.append(
                f"已暂缓 {len(attribution_claims)} 条与所引表格实验对象不一致的优化器陈述；"
                "论文题名不能代替具体实验的模型归属，请核对原页表注。"
            )
        if limitation_count:
            warnings.append(
                f"已分离 {limitation_count} 条输入证据不足说明；"
                "这些是当前检索范围的限制，不作为论文事实或已证实研究空白发布。"
            )
        if withheld:
            warnings.append(
                f"已暂缓发布 {withheld} 条只引用跨页未完结段落的陈述；请核对相邻页实验条件。"
            )
        claimed_ids = {item for claim in claims for item in claim.evidence_ids}
        covered_documents = {
            item.document_id for item in grounded.evidence if item.evidence_id in claimed_ids
        }
        uncovered = [
            item.title or item.original_filename
            for item in documents
            if item.id not in covered_documents
        ]
        if uncovered:
            warnings.append("以下文献尚无模型声明支持，请补充或审校：" + "、".join(uncovered))
        if grounded.provider == "extractive":
            warnings.append("当前框架使用抽取式原文整理，不是成功的生成式模型输出。")
        # Successful generation does not erase a retrieval/reranker fallback.
        warnings.extend(dict.fromkeys(
            step.summary for step in grounded.trace if step.status in {"fallback", "rejected"}
        ))
        if grounded.insufficient_evidence:
            warnings.append("当前证据不足，框架仅供组织研究问题，不应视作已支持的论文论证。")
        incomplete = [
            item.citation_key
            for item in references
            if not item.authors or item.publication_year is None
        ]
        if incomplete:
            warnings.append(f"{', '.join(incomplete)} 的作者或年份不完整，引用中仅保留已核验字段。")
        return WritingOutlineRead(
            proposed_title=proposed_title,
            idea=idea,
            sections=sections,
            references=references,
            claims=claims,
            deferred_claims=deferred,
            grounding_audit=audit,
            evidence=grounded.evidence,
            provider=grounded.provider,
            model=grounded.model,
            insufficient_evidence=grounded.insufficient_evidence or not claims,
            warnings=warnings,
        )
