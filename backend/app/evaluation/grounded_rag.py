from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.agent.harness import ResearchAgent
from app.agent.registry import SkillContext, SkillDefinition, SkillRegistry
from app.agent.skills import SearchEvidenceInput
from app.evaluation.models import (
    AnswerExpectation,
    EvidenceExpectation,
    GroundedRAGEvaluationSet,
)
from app.evaluation.retrieval import (
    _anchor_is_valid,
    _evidence_summary,
    _matches,
    _normalize_match_text,
    _percentile,
    _resolve_document_ids,
)
from app.llm import get_llm_provider
from app.llm.base import LLMProvider
from app.schemas.agent import AskRequest, EvidenceAnchor


class Retriever(Protocol):
    def search(
        self, question: str, document_ids: list[str] | None = None, top_k: int = 6
    ) -> list[EvidenceAnchor]: ...


async def evaluate_grounded_rag(
    session: Session,
    dataset: GroundedRAGEvaluationSet,
    *,
    provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
) -> dict:
    active_provider = provider or get_llm_provider()
    registry = _registry_for_retriever(retriever) if retriever is not None else None
    case_results: list[dict] = []
    latencies: list[int] = []
    total_answer_facts = 0
    matched_answer_facts = 0
    total_evidence_expectations = 0
    matched_evidence_expectations = 0
    matched_anchors = 0
    valid_anchors = 0
    total_citations = 0
    valid_citations = 0
    total_claims = 0
    grounded_claims = 0
    refusal_cases = 0
    passed_refusals = 0
    provider_calls = 0
    fallback_calls = 0
    provider_checks = 0
    provider_matches = 0

    for case in dataset.cases:
        total_answer_facts += len(case.answer_expectations)
        total_evidence_expectations += len(case.evidence_expectations)
        refusal_cases += int(case.must_refuse)
        document_ids, missing = _resolve_document_ids(session, case.documents)
        if missing:
            latencies.append(0)
            case_results.append(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "status": "missing_document",
                    "passed": False,
                    "must_refuse": case.must_refuse,
                    "missing_documents": missing,
                    "document_ids": document_ids,
                    "latency_ms": 0,
                    "answer_expectations": [],
                    "evidence_expectations": [],
                    "answer": "",
                    "claims": [],
                    "evidence": [],
                    "trace": [],
                }
            )
            continue

        agent = ResearchAgent(
            session,
            provider=active_provider,
            **({"registry": registry} if registry is not None else {}),
        )
        started = time.perf_counter()
        response = await agent.ask(
            AskRequest(
                question=case.question,
                document_ids=document_ids or None,
                top_k=case.top_k,
            )
        )
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        latencies.append(latency_ms)
        provider_checks += 1
        provider_match = (
            dataset.expected_provider is None or response.provider == dataset.expected_provider
        )
        provider_matches += int(provider_match)

        generation_step = next(
            (step for step in response.trace if step.skill == "llm.generate_grounded_answer"),
            None,
        )
        generation_status = generation_step.status if generation_step else "not_called"
        if generation_step:
            provider_calls += 1
            fallback_calls += int(generation_status == "fallback")

        answer_searchable = "\n".join(
            [response.answer, *(claim.text for claim in response.claims)]
        ).casefold()
        answer_results = []
        for expectation in case.answer_expectations:
            matched = _answer_matches(answer_searchable, expectation)
            matched_answer_facts += int(matched)
            answer_results.append({"expected": expectation.model_dump(), "matched": matched})

        evidence_results = []
        matched_gold_evidence_ids: set[str] = set()
        case_anchors_valid = True
        for expectation in case.evidence_expectations:
            matched = next(
                (anchor for anchor in response.evidence if _matches(anchor, expectation)),
                None,
            )
            if matched is None:
                evidence_results.append({"expected": expectation.model_dump(), "evidence_id": None})
                continue
            anchor_valid = _anchor_is_valid(
                matched,
                require_bbox=dataset.thresholds.require_bbox,
            )
            matched_evidence_expectations += 1
            matched_anchors += 1
            valid_anchors += int(anchor_valid)
            case_anchors_valid = case_anchors_valid and anchor_valid
            matched_gold_evidence_ids.add(matched.evidence_id)
            evidence_results.append(
                {
                    "expected": expectation.model_dump(),
                    "evidence_id": matched.evidence_id,
                    "anchor_valid": anchor_valid,
                }
            )

        evidence_by_id = {item.evidence_id: item for item in response.evidence}
        case_citations = [
            evidence_id for claim in response.claims for evidence_id in claim.evidence_ids
        ]
        case_valid_citations = [
            evidence_id for evidence_id in case_citations if evidence_id in evidence_by_id
        ]
        total_citations += len(case_citations)
        valid_citations += len(case_valid_citations)
        total_claims += len(response.claims)
        case_grounded_claims = sum(
            _claim_is_grounded(
                claim.text,
                claim.evidence_ids,
                evidence_by_id,
                case.evidence_expectations,
            )
            for claim in response.claims
        )
        grounded_claims += case_grounded_claims

        if case.must_refuse:
            refusal_passed = response.insufficient_evidence and not response.claims
            passed_refusals += int(refusal_passed)
            passed = refusal_passed and provider_match and generation_status != "fallback"
        else:
            passed = all(
                (
                    answer_results and all(item["matched"] for item in answer_results),
                    evidence_results and all(item["evidence_id"] for item in evidence_results),
                    case_anchors_valid,
                    bool(response.claims),
                    len(case_valid_citations) == len(case_citations),
                    case_grounded_claims == len(response.claims),
                    not response.insufficient_evidence,
                    generation_status == "ok",
                    provider_match,
                )
            )

        case_results.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "status": "passed" if passed else "failed",
                "passed": passed,
                "must_refuse": case.must_refuse,
                "missing_documents": [],
                "document_ids": document_ids,
                "latency_ms": latency_ms,
                "provider": response.provider,
                "model": response.model,
                "provider_match": provider_match,
                "generation_status": generation_status,
                "insufficient_evidence": response.insufficient_evidence,
                "answer_expectations": answer_results,
                "evidence_expectations": evidence_results,
                "matched_gold_evidence_ids": sorted(matched_gold_evidence_ids),
                "answer": response.answer,
                "claims": [claim.model_dump() for claim in response.claims],
                "evidence": [
                    _evidence_summary(anchor, rank)
                    for rank, anchor in enumerate(response.evidence, start=1)
                ],
                "trace": [step.model_dump() for step in response.trace],
            }
        )

    case_count = len(dataset.cases)
    passed_cases = sum(result["passed"] for result in case_results)
    metrics = {
        "case_pass_rate": _ratio(passed_cases, case_count),
        "answer_fact_recall": _ratio(matched_answer_facts, total_answer_facts),
        "evidence_recall": _ratio(
            matched_evidence_expectations,
            total_evidence_expectations,
        ),
        "anchor_valid_rate": _ratio(valid_anchors, matched_anchors),
        "citation_valid_rate": _ratio(valid_citations, total_citations),
        "grounded_claim_rate": _ratio(grounded_claims, total_claims),
        "refusal_pass_rate": _ratio(passed_refusals, refusal_cases),
        "provider_match_rate": _ratio(provider_matches, provider_checks),
        "fallback_rate": _ratio(fallback_calls, provider_calls, empty_value=0.0),
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "passed_cases": passed_cases,
        "total_cases": case_count,
        "provider_calls": provider_calls,
        "fallback_calls": fallback_calls,
        "matched_answer_facts": matched_answer_facts,
        "total_answer_facts": total_answer_facts,
        "matched_evidence_expectations": matched_evidence_expectations,
        "total_evidence_expectations": total_evidence_expectations,
    }
    failures = _threshold_failures(metrics, dataset)
    return {
        "schema_version": "1.0",
        "dataset_id": dataset.dataset_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not failures else "failed",
        "expected_provider": dataset.expected_provider,
        "provider": active_provider.name,
        "model": active_provider.model,
        "threshold_failures": failures,
        "metrics": metrics,
        "cases": case_results,
    }


def _registry_for_retriever(retriever: Retriever) -> SkillRegistry:
    registry = SkillRegistry()

    def search(payload: SearchEvidenceInput, _: SkillContext) -> list[EvidenceAnchor]:
        return retriever.search(
            payload.question,
            document_ids=payload.document_ids,
            top_k=payload.top_k,
        )

    registry.register(
        SkillDefinition(
            name="search_evidence",
            description="Evaluation-scoped evidence retriever.",
            input_model=SearchEvidenceInput,
            handler=search,
        )
    )
    return registry


def _answer_matches(searchable: str, expectation: AnswerExpectation) -> bool:
    normalized = _normalize_match_text(searchable)
    return any(
        _normalize_match_text(value) in normalized for value in expectation.text_contains_any
    )


def _claim_is_grounded(
    claim_text: str,
    evidence_ids: list[str],
    evidence_by_id: dict[str, EvidenceAnchor],
    expectations: list[EvidenceExpectation],
) -> bool:
    cited = [
        evidence_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in evidence_by_id
    ]
    if not cited:
        return False
    if any(_matches(anchor, expectation) for anchor in cited for expectation in expectations):
        return True
    return _claim_has_deterministic_overlap(claim_text, cited)


def _claim_has_deterministic_overlap(
    claim_text: str,
    evidence: list[EvidenceAnchor],
) -> bool:
    """Conservative cross-language proxy for claims beyond the gold answer facts."""
    claim_tokens = _fact_tokens(claim_text)
    evidence_tokens = _fact_tokens("\n".join(anchor.quote for anchor in evidence))
    if not claim_tokens:
        return False
    numeric_tokens = {token for token in claim_tokens if token[0].isdigit()}
    if numeric_tokens and not numeric_tokens.issubset(evidence_tokens):
        return False
    overlap = claim_tokens & evidence_tokens
    required = 1 if len(claim_tokens) == 1 else 2
    return len(overlap) >= required and len(overlap) / len(claim_tokens) >= 0.5


def _fact_tokens(value: str) -> set[str]:
    value = re.sub(r"(?<=\d)\s*([.,])\s*(?=\d)", r"\1", value)
    return set(
        token.casefold().replace(",", ".")
        for token in re.findall(r"\d+(?:[.,]\d+)?|[A-Za-z]+[A-Za-z0-9_.-]*", value)
        if len(token) >= 2 or token[0].isdigit()
    )


def _ratio(numerator: int, denominator: int, *, empty_value: float = 1.0) -> float:
    if denominator == 0:
        return empty_value
    return round(numerator / denominator, 4)


def _threshold_failures(metrics: dict, dataset: GroundedRAGEvaluationSet) -> list[str]:
    thresholds = dataset.thresholds
    minimums = {
        "case_pass_rate": thresholds.min_case_pass_rate,
        "answer_fact_recall": thresholds.min_answer_fact_recall,
        "evidence_recall": thresholds.min_evidence_recall,
        "anchor_valid_rate": thresholds.min_anchor_valid_rate,
        "citation_valid_rate": thresholds.min_citation_valid_rate,
        "grounded_claim_rate": thresholds.min_grounded_claim_rate,
        "refusal_pass_rate": thresholds.min_refusal_pass_rate,
        "provider_match_rate": thresholds.min_provider_match_rate,
    }
    failures = [
        f"{metric}={metrics[metric]} < {minimum}"
        for metric, minimum in minimums.items()
        if metrics[metric] < minimum
    ]
    if metrics["fallback_rate"] > thresholds.max_fallback_rate:
        failures.append(
            f"fallback_rate={metrics['fallback_rate']} > {thresholds.max_fallback_rate}"
        )
    return failures
