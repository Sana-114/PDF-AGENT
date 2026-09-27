"""Audit live BGE + DeepSeek comparisons against SHA-pinned source-PDF facts."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, init_db  # noqa: E402
from app.evaluation.retrieval import (  # noqa: E402
    _anchor_is_valid,
    _normalize_match_text,
)
from app.llm import get_llm_provider  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.services.paper_comparison import (  # noqa: E402
    PaperComparisonService,
    _unsupported_numeric_facts,
)


class RecordingProvider:
    """Capture the unfiltered model response for audit without changing publication."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.name = delegate.name
        self.model = delegate.model
        self.supports_translation = delegate.supports_translation
        self.generated_claims = []

    async def generate_grounded_answer(self, question, evidence):
        result = await self.delegate.generate_grounded_answer(question, evidence)
        self.generated_claims = result.claims
        return result


def _contains_any(text: str, options: list[str]) -> bool:
    searchable = _normalize_match_text(text)
    return any(_normalize_match_text(option) in searchable for option in options)


def _resolve_documents(session, filenames: list[str], hashes: dict[str, str]) -> list[Document]:
    documents = []
    for filename in filenames:
        if filename not in hashes:
            raise ValueError(f"No pinned SHA-256 for {filename}")
        matches = session.scalars(
            select(Document).where(
                Document.original_filename == filename,
                Document.sha256 == hashes[filename],
                Document.status == DocumentStatus.READY,
            )
        ).all()
        if len(matches) != 1:
            raise ValueError(
                f"Expected one ready document with pinned SHA-256 for {filename}; "
                f"found {len(matches)}"
            )
        documents.append(matches[0])
    return documents


def _check_fact(fact: dict, response, document_ids: dict[str, str]) -> bool:
    document_id = document_ids[fact["source_filename"]]
    anchors = {item.evidence_id: item for item in response.evidence}
    for claim in response.claims:
        if not _contains_any(claim.text, fact["claim_contains_any"]):
            continue
        if any(
            anchor is not None
            and anchor.document_id == document_id
            and anchor.page_number in fact["source_pages"]
            and _contains_any(anchor.quote, fact["source_text_contains_any"])
            for anchor in (anchors.get(item) for item in claim.evidence_ids)
        ):
            return True
    return False


def _check_case(case: dict, response, document_ids: dict[str, str], provider) -> dict:
    failures: list[str] = []
    must_refuse = case.get("must_refuse", False)
    expected_mode = "reranked"
    if response.provider != "deepseek" or response.model != provider.model:
        failures.append("provider_or_model_mismatch")
    if must_refuse:
        if not response.insufficient_evidence or response.audit.cross_document_claim_count:
            failures.append("unsupported_question_not_refused")
        quantity_pattern = case.get("forbidden_quantity_pattern")
        if quantity_pattern and any(
            re.search(quantity_pattern, claim.text, flags=re.I) for claim in response.claims
        ):
            failures.append("invented_target_quantity")
    else:
        if response.insufficient_evidence:
            failures.append("insufficient_evidence")
        if response.audit.cross_document_claim_count < case["min_cross_document_claims"]:
            failures.append("missing_cross_document_claims")
        if response.audit.referenced_document_count < len(document_ids):
            failures.append("not_all_documents_cited")
    if not response.evidence or any(
        item.retrieval_mode != expected_mode for item in response.evidence
    ):
        failures.append("retrieval_bypassed_reranker")
    if any(not _anchor_is_valid(item, require_bbox=True) for item in response.evidence):
        failures.append("invalid_pdf_anchor")
    generation_steps = [item for item in response.trace if item.skill == "llm.compare_documents"]
    if len(generation_steps) != 1 or generation_steps[0].status != "ok":
        failures.append("llm_fallback")
    anchors = {item.evidence_id: item for item in response.evidence}
    for claim in response.claims:
        cited = [anchors.get(item) for item in claim.evidence_ids]
        if not cited or any(item is None for item in cited):
            failures.append("invalid_claim_citation")
            continue
        if _unsupported_numeric_facts(claim.text, cited):
            failures.append("published_unsupported_number")
        if _contains_any(claim.text, case.get("forbidden_claim_text_contains_any", [])):
            failures.append("forbidden_related_work_attribution")
    fact_checks = {
        fact["fact_id"]: _check_fact(fact, response, document_ids)
        for fact in case["required_claim_facts"]
    }
    failures.extend(f"missing_fact:{key}" for key, passed in fact_checks.items() if not passed)
    return {"passed": not failures, "failures": list(dict.fromkeys(failures)), "facts": fact_checks}


async def evaluate(dataset: dict, manifest: dict) -> dict:
    if not settings.vector_search_enabled or settings.reranker_provider != "tei":
        raise ValueError("Standard BGE vector search and TEI reranker must be enabled")
    delegate = get_llm_provider()
    if delegate.name != dataset["expected_provider"]:
        raise ValueError("DeepSeek provider must be active for this evaluation")
    hashes = {
        item["filename"]: item["sha256"] for item in manifest["downloads"] if item.get("sha256")
    }
    results = []
    init_db()
    with SessionLocal() as session:
        for case in dataset["cases"]:
            documents = _resolve_documents(session, case["documents"], hashes)
            document_ids = {document.original_filename: document.id for document in documents}
            provider = RecordingProvider(delegate)
            started = time.perf_counter()
            response = await PaperComparisonService(session, provider=provider).compare(
                documents,
                case["question"],
                evidence_per_document=case.get("evidence_per_document", 4),
            )
            checks = _check_case(case, response, document_ids, provider)
            results.append(
                {
                    "case_id": case["case_id"],
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "document_ids": document_ids,
                    "source_sha256": {filename: hashes[filename] for filename in case["documents"]},
                    **checks,
                    "response": response.model_dump(mode="json"),
                    "raw_generated_claims": [asdict(item) for item in provider.generated_claims],
                }
            )
    return {
        "schema_version": "1.0",
        "dataset_id": dataset["dataset_id"],
        "status": "passed" if all(item["passed"] for item in results) else "failed",
        "provider": delegate.name,
        "model": delegate.model,
        "required_retrieval_mode": dataset["required_retrieval_mode"],
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "evals/real_pdf_comparison_grounded.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT.parent / "docs/pdf-regression-corpus.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = asyncio.run(evaluate(dataset, manifest))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": report["status"],
                "provider": report["provider"],
                "model": report["model"],
                "required_retrieval_mode": report["required_retrieval_mode"],
                "cases": [
                    {
                        "case_id": item["case_id"],
                        "passed": item["passed"],
                        "facts": item["facts"],
                        "failures": item["failures"],
                        "latency_ms": item["latency_ms"],
                    }
                    for item in report["cases"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
