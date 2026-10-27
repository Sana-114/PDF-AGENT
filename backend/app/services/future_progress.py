"""Bounded model triage of selected later work, separate from human resolution.

No whole-library sends, silent retries, inferred dates or universal 'resolved'
state. Bounded recommendation exclusion requires an additional source review.
"""

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.future_work import future_goal_quotes
from app.llm.base import GeneratedClaim, LLMConfigurationError, LLMResponseError
from app.models.future_work import FutureWorkAssessment
from app.models.paper_source import PaperSource
from app.schemas.agent import EvidenceAnchor
from app.schemas.review import (
    FutureExclusionReviewRead,
    FutureFacetRead,
    FutureProgressProof,
    FutureWorkAssessmentRead,
    FutureWorkAssessmentRequest,
)
from app.services.future_work_review import direction_key, verify_passage
from app.services.grounded_text import audit_grounded_claims


def _validated_inputs(db, key, request):
    from app.services.evidence_review import _extract_drafts, _publication_year

    document, block = verify_passage(db, request.source)
    source_year = _publication_year(document, db.get(PaperSource, document.id))
    from app.services.document_content import read_parsed_document

    if key != direction_key(
        document.id, request.source.page_number, request.source.block_ids, request.source.quote
    ):
        raise ValueError("方向ID与原文不一致。")
    if not any(
        d.kind == "future_work"
        and direction_key(d.document_id, d.page_number, d.block_ids, d.quote) == key
        for d in _extract_drafts(document, read_parsed_document(document.id), source_year)
    ):
        raise ValueError("该来源不是当前识别出的未来工作段落。")
    source = EvidenceAnchor(
        evidence_id="S0",
        document_id=document.id,
        document_title=document.title,
        quote=request.source.quote,
        page_number=request.source.page_number,
        block_ids=request.source.block_ids,
        bbox=block.get("bbox"),
        score=1,
    )
    progress = []
    identities = set()
    for index, item in enumerate(request.progress, 1):
        later, later_block = verify_passage(db, item)
        year = _publication_year(later, db.get(PaperSource, later.id))
        if later.id == document.id or source_year is None or year is None or year <= source_year:
            raise ValueError("进展核对需要另一篇可核验且更晚发表的论文。")
        identity = (later.id, item.page_number, tuple(item.block_ids), item.quote)
        if identity in identities:
            raise ValueError("不能重复提交同一段后续证据。")
        identities.add(identity)
        progress.append(
            EvidenceAnchor(
                evidence_id=f"P{index}",
                document_id=later.id,
                document_title=later.title,
                page_number=item.page_number,
                block_ids=item.block_ids,
                quote=item.quote,
                bbox=later_block.get("bbox"),
                score=1,
            )
        )
    return source, progress


def _scope_terms(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    words = set(re.findall(r"[a-z]{3,}", text)) - {
        "the",
        "and",
        "with",
        "our",
        "this",
        "that",
        "which",
        "from",
        "for",
        "into",
        "are",
        "will",
        "plan",
        "future",
        "work",
        "research",
        "hope",
        "such",
        "other",
    }
    for span in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        words.update(span[i : i + 2] for i in range(len(span) - 1))
    return words


def _literal_pdf_span(quote, source):
    """Map whitespace/Latin word-wrap variation back to ONE literal source span.

    Preserve letters, case, punctuation, hyphens and numbers. In particular this
    cannot repair digits or treat a mathematical x- y as a word-wrap artifact.
    The returned value, not the normalized draft, is published and persisted.
    """
    if quote in source:
        return quote
    normalized = " ".join(quote.split())
    normalized = re.sub(r"\b([A-Za-z]{3,})-\s+([A-Za-z]{2,})\b", r"\1-\2", normalized)
    pattern = re.escape(normalized).replace("\\ ", r"\s+")
    pattern = re.sub(r"([A-Za-z]{3,})\\-([A-Za-z]{2,})", lambda m: m[1] + r"-\s*" + m[2], pattern)
    matches = list(re.finditer(pattern, source))
    return matches[0].group() if len(matches) == 1 else None


def validate_assessment(raw, source, progress):
    if not isinstance(raw, dict) or set(raw) != {"facets"}:
        raise LLMResponseError("进展核对结构无效。")
    facets = raw["facets"]
    if not isinstance(facets, list) or not 1 <= len(facets) <= 8:
        raise LLMResponseError("进展核对必须包含1至8个范围项。")
    anchors = {e.evidence_id: e for e in progress}
    checked, warnings, goals = [], [], set()
    eligible_goals = future_goal_quotes(source.quote)
    for item in facets:
        if not isinstance(item, dict) or set(item) != {
            "goal_quote",
            "status",
            "explanation",
            "proof",
        }:
            raise LLMResponseError("进展范围项结构无效。")
        goal, status, explanation, proof = (
            item[k] for k in ("goal_quote", "status", "explanation", "proof")
        )
        original_goal = _literal_pdf_span(goal, source.quote) if isinstance(goal, str) else None
        if (
            not isinstance(goal, str)
            or not 6 <= len(goal) <= 1600
            or not original_goal
            or not any(original_goal in candidate for candidate in eligible_goals)
            or original_goal in goals
            or not isinstance(status, str)
            or status not in {"addressed", "partial", "not_established"}
            or not isinstance(explanation, str)
            or not 1 <= len(explanation.strip()) <= 500
            or not isinstance(proof, list)
            or len(proof) > 3
        ):
            raise LLMResponseError("范围项必须逐字对应原计划，并保留明确状态和说明。")
        if original_goal != goal:
            warnings.append("范围项的PDF断行空白已映射回唯一原文片段；未更改字母、标点或数值。")
        goal = original_goal
        goals.add(goal)
        if (status == "not_established" and proof) or (status != "not_established" and not proof):
            raise LLMResponseError("有进展的范围项必须提供原文证明；未建立项不能借用引用。")
        selected = []
        for p in proof:
            if not isinstance(p, dict) or set(p) != {"evidence_id", "quote"}:
                raise LLMResponseError("后续进展证明结构无效。")
            key, quote = p["evidence_id"], p["quote"]
            original_quote = (
                _literal_pdf_span(quote, anchors[key].quote)
                if isinstance(key, str) and key in anchors and isinstance(quote, str)
                else None
            )
            if (
                not isinstance(key, str)
                or key not in anchors
                or not isinstance(quote, str)
                or len(quote.strip()) < 10
                or not original_quote
            ):
                raise LLMResponseError("后续进展必须引用实际发送的编号及逐字原文。")
            selected.append(anchors[key].model_copy(update={"quote": original_quote}))
        identities = {(e.evidence_id, e.quote) for e in selected}
        if len(identities) != len(selected):
            raise LLMResponseError("同一范围项不能重复提交相同的原文证明。")
        ids = list(dict.fromkeys(e.evidence_id for e in selected))
        published_proof = [
            FutureProgressProof(evidence_id=e.evidence_id, quote=e.quote) for e in selected
        ]
        if selected:
            # Multiple distinct spans from one selected paragraph are valid.
            # Aggregate only verified spans for the internal numeric/scope audit
            # so duplicate IDs cannot silently replace earlier proof text.
            # Published/persisted proofs remain separate literal source spans.
            audit_progress = [
                anchors[key].model_copy(update={
                    "quote": "\n…\n".join(e.quote for e in selected if e.evidence_id == key),
                }) for key in ids
            ]
            accepted, _ = audit_grounded_claims(
                [GeneratedClaim(explanation, ["S0", *ids])], [source, *audit_progress]
            )
            if not accepted:
                status, ids, published_proof = "not_established", [], []
                warnings.append("有范围项未通过发布检查；保留为未建立，不补造依据。")
        if status == "not_established":
            explanation = "所选更晚摘录不足以确认这一目标已有对应结果；不据此判断全球研究现状。"
        checked.append(
            FutureFacetRead(
                goal_quote=goal,
                status=status,
                explanation=explanation,
                evidence_ids=ids,
                proof=published_proof,
            )
        )
    # Background definitions and completed results are context, not goals.
    # Every explicit plan must still be covered, including compound restrictions.
    coverage = bool(eligible_goals) and all(
        _scope_terms(goal) <= _scope_terms(" ".join(goals)) for goal in eligible_goals
    )
    if not coverage:
        warnings.append("范围摘录未覆盖全部明确计划及其限制；不能当作完整目标已得到回答。")
    any_progress = any(f.status in {"addressed", "partial"} for f in checked)
    status = (
        "supported_progress"
        if coverage and all(f.status == "addressed" for f in checked)
        else "partial_progress"
        if any_progress
        else "insufficient"
    )
    warnings.append(
        "模型仅核对所选摘录；词项覆盖和逐字引用不保证语义正确，不能证明整个方向已解决。"
    )
    return status, checked, coverage, warnings


def validate_exclusion_review(raw, count):
    allowed = {"supported", "unsupported", "uncertain"}
    if (not isinstance(raw, dict) or set(raw) != {"scope_check", "facet_checks"}
            or not isinstance(raw["scope_check"], str) or raw["scope_check"] not in allowed
            or not isinstance(raw["facet_checks"], list) or len(raw["facet_checks"]) != count
            or any(not isinstance(s, str) or s not in allowed for s in raw["facet_checks"])):
        raise LLMResponseError("排除校审必须覆盖完整目标和每个范围项。")
    approved = raw["scope_check"] == "supported" and all(
        s == "supported" for s in raw["facet_checks"]
    )
    return FutureExclusionReviewRead(status="approved" if approved else "rejected", **raw)


def exclusion_is_supported(status, coverage, facets, review):
    return bool(status == "supported_progress" and coverage and facets
                and all(f.status == "addressed" and f.proof for f in facets)
                and review and review.status == "approved"
                and review.scope_check == "supported"
                and len(review.facet_checks) == len(facets)
                and all(s == "supported" for s in review.facet_checks))


async def assess_progress(db: Session, key: str, request, provider):
    source, progress = _validated_inputs(db, key, request)
    generate = getattr(provider, "assess_future_work", None)
    if generate is None:
        raise LLMConfigurationError("当前模型不支持后续进展核对。")
    raw = await generate(source.quote, [e.model_dump() for e in progress])
    if (source, progress) != _validated_inputs(db, key, request):
        raise ValueError("模型核对期间来源发生变化，未保存旧结论。")
    status, facets, coverage, warnings = validate_assessment(raw, source, progress)
    exclusion_review = None
    if status == "supported_progress":
        review = getattr(provider, "review_future_exclusion", None)
        try:
            if not callable(review):
                raise LLMConfigurationError("当前模型不支持独立排除校审。")
            exclusion_review = validate_exclusion_review(
                await review(source, progress, facets), len(facets)
            )
        except (LLMConfigurationError, LLMResponseError):
            exclusion_review = FutureExclusionReviewRead(
                status="unavailable", scope_check="uncertain", facet_checks=[],
            )
            warnings.append("独立排除校审不可用；保留原方向，不自动重试。")
        if (source, progress) != _validated_inputs(db, key, request):
            raise ValueError("排除校审期间来源发生变化，未保存旧结论。")
    excluded = exclusion_is_supported(status, coverage, facets, exclusion_review)
    if excluded:
        warnings.append("已从开放方向推荐中排除所选原计划；仅适用于这些目标与后续证据，可人工改回。")
    result = FutureWorkAssessmentRead(
        direction_id=key,
        status=status,
        facets=facets,
        progress_evidence=progress,
        scope_coverage_complete=coverage,
        warnings=warnings,
        provider=provider.name,
        model=provider.model,
        updated_at=datetime.now(UTC),
        excluded_from_recommendations=excluded,
        exclusion_review=exclusion_review,
    )
    row = db.get(FutureWorkAssessment, key)
    if row is None:
        row = FutureWorkAssessment(direction_id=key, source_document_id=source.document_id)
        db.add(row)
    row.request_json, row.result_json = request.model_dump_json(), result.model_dump_json()
    db.commit()
    return result


def read_assessment(db, key):
    row = db.get(FutureWorkAssessment, key)
    if row is None:
        return None
    request = FutureWorkAssessmentRequest.model_validate_json(row.request_json)
    source, progress = _validated_inputs(db, key, request)
    result = FutureWorkAssessmentRead.model_validate_json(row.result_json)
    if result.direction_id != key or result.progress_evidence != progress:
        raise ValueError("保存的进展证据或位置已变更，需要重新核对。")
    if any(f.goal_quote not in source.quote for f in result.facets):
        raise ValueError("保存的范围摘录无法复核。")
    status, facets, coverage, _ = validate_assessment(
        {
            "facets": [
                {
                    "goal_quote": f.goal_quote,
                    "status": f.status,
                    "explanation": f.explanation,
                    "proof": [p.model_dump() for p in f.proof],
                }
                for f in result.facets
            ]
        },
        source,
        progress,
    )
    if (
        status != result.status
        or facets != result.facets
        or coverage != result.scope_coverage_complete
        or result.excluded_from_recommendations != exclusion_is_supported(
            status, coverage, facets, result.exclusion_review
        )
    ):
        raise ValueError("保存的进展结论不符合当前检查，需重新核对。")
    return result
