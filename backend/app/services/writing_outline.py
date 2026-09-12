import json
import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.harness import ResearchAgent
from app.models.document import Document
from app.models.paper_source import PaperSource
from app.schemas.agent import AskRequest, EvidenceAnchor
from app.schemas.writing import (
    VerifiedReferenceRead,
    WritingOutlineRead,
    WritingSectionRead,
)

YEAR = re.compile(r"(?:19|20)\d{2}")


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


def _year(document: Document, metadata: dict[str, Any]) -> int | None:
    for value in (metadata.get("year"), metadata.get("published_at")):
        match = YEAR.search(str(value or ""))
        if match:
            return int(match.group())
    arxiv_id = document.arxiv_id or str(metadata.get("arxiv_id") or "")
    match = re.match(r"(\d{2})\d{2}\.\d{4,5}", arxiv_id)
    return 2000 + int(match.group(1)) if match else None


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
        title = document.title or document.original_filename
        authors = _json_list(document.authors_json) or [
            str(item) for item in metadata.get("authors", []) if str(item).strip()
        ]
        publication_year = _year(document, metadata)
        venue = str(metadata.get("venue") or "").strip() or None
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
    supported_claims = [
        f"- {item.text} [{' '.join(item.evidence_ids)}]" for item in claims[:6]
    ]
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
        items = evidence_by_document[reference.document_id][:2]
        related_evidence_ids.extend(item.evidence_id for item in items)
        snippets = " ".join(
            f"{item.quote[:360]} [{item.evidence_id}]" for item in items
        )
        related_lines.append(
            f"- **[{reference.citation_key}] {reference.title}**：{snippets}"
        )
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
                dict.fromkeys(
                    item for claim in claims[:2] for item in claim.evidence_ids
                )
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
        language_instruction = "中文" if language == "zh" else "English"
        request = AskRequest(
            question=(
                f"围绕以下研究 idea 提取可用于论文框架的已有事实、研究缺口和相关工作：{idea}。"
                f"请使用{language_instruction}，只依据证据，不要生成或猜测参考文献。"
            ),
            document_ids=[item.id for item in documents],
            top_k=top_k,
        )
        grounded = await self.agent.ask(request)
        references = _reference_records(self.session, grounded.evidence)
        sections = _section_content(
            idea,
            references,
            grounded.evidence,
            grounded.claims,
            language,
        )
        proposed_title = idea[:120]
        warnings: list[str] = []
        incomplete = [
            item.citation_key
            for item in references
            if not item.authors or item.publication_year is None
        ]
        if incomplete:
            warnings.append(
                f"{', '.join(incomplete)} 的作者或年份不完整，引用中仅保留已核验字段。"
            )
        return WritingOutlineRead(
            proposed_title=proposed_title,
            idea=idea,
            sections=sections,
            references=references,
            claims=grounded.claims,
            evidence=grounded.evidence,
            provider=grounded.provider,
            model=grounded.model,
            insufficient_evidence=grounded.insufficient_evidence,
            warnings=warnings,
        )
