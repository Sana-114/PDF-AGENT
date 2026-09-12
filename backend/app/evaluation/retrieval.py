from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluation.models import (
    DocumentSelector,
    EvidenceExpectation,
    RetrievalEvaluationSet,
)
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.retrieval import HybridRetriever


class Retriever(Protocol):
    def search(
        self, question: str, document_ids: list[str] | None = None, top_k: int = 6
    ) -> list[EvidenceAnchor]: ...


def evaluate_retrieval(
    session: Session,
    dataset: RetrievalEvaluationSet,
    *,
    retriever: Retriever | None = None,
) -> dict:
    active_retriever = retriever or HybridRetriever(session)
    case_results = []
    matched_expectations = 0
    total_expectations = 0
    valid_anchors = 0
    matched_anchors = 0
    reciprocal_ranks = []
    latencies = []

    for case in dataset.cases:
        document_ids, missing = _resolve_document_ids(session, case.documents)
        total_expectations += len(case.expectations)
        if missing:
            case_results.append(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "status": "missing_document",
                    "passed": False,
                    "missing_documents": missing,
                    "document_ids": document_ids,
                    "latency_ms": 0,
                    "reciprocal_rank": 0.0,
                    "expectations": [],
                    "evidence": [],
                }
            )
            reciprocal_ranks.append(0.0)
            latencies.append(0)
            continue

        started = time.perf_counter()
        evidence = active_retriever.search(
            case.question,
            document_ids=document_ids or None,
            top_k=case.top_k,
        )
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        latencies.append(latency_ms)
        expectation_results = []
        matched_ranks = []
        case_anchors_valid = True
        for expectation in case.expectations:
            matched = next(
                (
                    (rank, anchor)
                    for rank, anchor in enumerate(evidence, start=1)
                    if _matches(anchor, expectation)
                ),
                None,
            )
            if matched is None:
                expectation_results.append(
                    {"expected": expectation.model_dump(), "matched_rank": None}
                )
                continue
            rank, anchor = matched
            anchor_valid = _anchor_is_valid(
                anchor, require_bbox=dataset.thresholds.require_bbox
            )
            matched_expectations += 1
            matched_anchors += 1
            valid_anchors += int(anchor_valid)
            matched_ranks.append(rank)
            case_anchors_valid = case_anchors_valid and anchor_valid
            expectation_results.append(
                {
                    "expected": expectation.model_dump(),
                    "matched_rank": rank,
                    "evidence_id": anchor.evidence_id,
                    "anchor_valid": anchor_valid,
                }
            )

        all_expected_found = len(matched_ranks) == len(case.expectations)
        reciprocal_rank = 1 / min(matched_ranks) if matched_ranks else 0.0
        reciprocal_ranks.append(reciprocal_rank)
        case_results.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "status": "passed" if all_expected_found and case_anchors_valid else "failed",
                "passed": all_expected_found and case_anchors_valid,
                "missing_documents": [],
                "document_ids": document_ids,
                "latency_ms": latency_ms,
                "reciprocal_rank": round(reciprocal_rank, 4),
                "expectations": expectation_results,
                "evidence": [
                    _evidence_summary(anchor, rank)
                    for rank, anchor in enumerate(evidence, 1)
                ],
            }
        )

    case_count = len(dataset.cases)
    passed_cases = sum(result["passed"] for result in case_results)
    metrics = {
        "case_pass_rate": round(passed_cases / case_count, 4),
        "evidence_recall": round(matched_expectations / max(total_expectations, 1), 4),
        "mean_reciprocal_rank": round(sum(reciprocal_ranks) / case_count, 4),
        "anchor_valid_rate": round(valid_anchors / max(matched_anchors, 1), 4),
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "passed_cases": passed_cases,
        "total_cases": case_count,
        "matched_expectations": matched_expectations,
        "total_expectations": total_expectations,
    }
    failures = _threshold_failures(metrics, dataset)
    return {
        "schema_version": "1.0",
        "dataset_id": dataset.dataset_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not failures else "failed",
        "threshold_failures": failures,
        "metrics": metrics,
        "cases": case_results,
    }


def _resolve_document_ids(
    session: Session, selectors: list[DocumentSelector]
) -> tuple[list[str], list[dict]]:
    if not selectors:
        return [], []
    documents = session.scalars(
        select(Document)
        .where(Document.status == DocumentStatus.READY)
        .order_by(Document.created_at.desc(), Document.id)
    ).all()
    resolved = []
    missing = []
    for selector in selectors:
        match = next(
            (document for document in documents if _document_matches(document, selector)),
            None,
        )
        if match is None:
            missing.append(selector.model_dump(exclude_none=True))
        elif match.id not in resolved:
            resolved.append(match.id)
    return resolved, missing


def _document_matches(document: Document, selector: DocumentSelector) -> bool:
    checks = []
    if selector.document_id:
        checks.append(document.id == selector.document_id)
    if selector.filename:
        checks.append(document.original_filename.casefold() == selector.filename.casefold())
    if selector.title_contains:
        checks.append(selector.title_contains.casefold() in (document.title or "").casefold())
    if selector.arxiv_id:
        checks.append(document.arxiv_id == selector.arxiv_id)
    if selector.arxiv_version is not None:
        checks.append(document.arxiv_version == selector.arxiv_version)
    return all(checks)


def _matches(anchor: EvidenceAnchor, expectation: EvidenceExpectation) -> bool:
    searchable = f"{anchor.section or ''}\n{anchor.quote}".casefold()
    if not any(value.casefold() in searchable for value in expectation.text_contains_any):
        return False
    if expectation.page_numbers and anchor.page_number not in expectation.page_numbers:
        return False
    if expectation.source_types and anchor.source_type not in expectation.source_types:
        return False
    expected_title = expectation.document_title_contains
    if expected_title and expected_title.casefold() not in (
        anchor.document_title or ""
    ).casefold():
        return False
    return True


def _anchor_is_valid(anchor: EvidenceAnchor, *, require_bbox: bool) -> bool:
    if anchor.page_number < 1 or not anchor.block_ids:
        return False
    if not require_bbox:
        return True
    return bool(
        anchor.bbox
        and len(anchor.bbox) == 4
        and all(math.isfinite(value) for value in anchor.bbox)
    )


def _evidence_summary(anchor: EvidenceAnchor, rank: int) -> dict:
    return {
        "rank": rank,
        "evidence_id": anchor.evidence_id,
        "document_id": anchor.document_id,
        "document_title": anchor.document_title,
        "page_number": anchor.page_number,
        "source_type": anchor.source_type,
        "section": anchor.section,
        "score": anchor.score,
        "retrieval_mode": anchor.retrieval_mode,
        "block_ids": anchor.block_ids,
        "bbox": anchor.bbox,
        "quote_preview": anchor.quote[:320],
    }


def _threshold_failures(metrics: dict, dataset: RetrievalEvaluationSet) -> list[str]:
    thresholds = dataset.thresholds
    checks = {
        "case_pass_rate": thresholds.min_case_pass_rate,
        "evidence_recall": thresholds.min_evidence_recall,
        "mean_reciprocal_rank": thresholds.min_mean_reciprocal_rank,
        "anchor_valid_rate": thresholds.min_anchor_valid_rate,
    }
    return [
        f"{metric}={metrics[metric]} < {minimum}"
        for metric, minimum in checks.items()
        if metrics[metric] < minimum
    ]


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil((len(ordered) - 1) * quantile))
    return ordered[index]
