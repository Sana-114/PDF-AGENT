"""Generate one real 20-paper review from an already audited isolated official fixture."""

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from verify_official_local_features import GRAPH_FILES


async def run(args) -> int:
    fixture = args.fixture.resolve()
    if not fixture.is_relative_to(TEMP_ROOT) or fixture == TEMP_ROOT or not args.allow_cloud:
        raise ValueError("Requires an isolated official fixture and explicit --allow-cloud")
    rows = json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))
    selected = [next(x for x in rows if x["filename"] == name) for name in GRAPH_FILES]
    for row in selected:
        if (
            digest(fixture / "uploads" / (row["document_id"] + ".pdf")) != row["source_sha256"]
            or digest(fixture / "parsed" / (row["document_id"] + ".json")) != row["ast_sha256"]
        ):
            raise ValueError("Fixture source/AST changed")
    for key, value in {
        "DATABASE_URL": f"sqlite:///{(fixture / 'acceptance.db').as_posix()}",
        "DATA_DIR": str(fixture),
        "UPLOAD_DIR": str(fixture / "uploads"),
        "PARSED_DIR": str(fixture / "parsed"),
        "TRANSLATION_DIR": str(fixture / "translations"),
    }.items():
        os.environ[key] = value
    from app.core.database import SessionLocal
    from app.llm.factory import get_llm_provider
    from app.models.document import Document
    from app.services.evidence_review import EvidenceReviewService

    root = isolated_output(args.output_dir)
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Requires real DeepSeek")
    original_post = provider._post_response
    calls = []

    async def recorded_post(payload):
        record = {"request": payload, "status": "started"}
        calls.append(record)
        write_json(root / "calls.json", calls)
        try:
            raw = await original_post(payload)
            record.update(status="returned", response=raw)
            return raw
        except Exception as exc:
            record.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(root / "calls.json", calls)

    provider._post_response = recorded_post
    report = {
        "status": "running",
        "sources": selected,
        "automatic_retries": 0,
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "fixture_sources_reused_not_reparsed": True,
        "production_library_modified": False,
        "scope": (
            "Real generated review, source coverage and citation/numeric guards; "
            "semantic audit recorded separately"
        ),
    }
    write_json(root / "report.json", report)
    with SessionLocal() as db:
        documents = [db.get(Document, row["document_id"]) for row in selected]
        review = await EvidenceReviewService(db, provider=provider).generate(
            documents, max_evidence=getattr(args, "max_evidence", 40)
        )
    write_json(root / "generated-review.json", review.model_dump(mode="json"))
    evidence_ids = {e.evidence_id for e in review.evidence}
    valid_claims = bool(review.claims) and all(
        c.evidence_ids and set(c.evidence_ids) <= evidence_ids for c in review.claims
    )
    coverage = {e.document_id for e in review.evidence}
    report.update(
        status="source_checks_passed"
        if review.provider == "deepseek" and valid_claims and len(coverage) == 20
        else "failed",
        cloud_calls=len(calls),
        selected_papers=20,
        evidence_papers=len(coverage),
        evidence_count=len(review.evidence),
        local_future_workflow_evidence=len(review.future_work_evidence),
        local_future_candidates=len(review.future_directions),
        published_claims=len(review.claims),
        themes=[theme.model_dump() for theme in review.themes],
        provider=review.provider,
        model=review.model,
        warnings=review.warnings,
        valid_citation_links=valid_claims,
        semantic_audit="pending",
        citation_relations=len(review.citation_relations),
        evidence_budget=getattr(args, "max_evidence", 40),
        brevity={
            "claims": len(review.claims),
            "rendered_chars": len(review.review),
            "within_requested_length": len(review.claims) <= 8 and len(review.review) <= 1600,
        },
        multi_source_claims=sum(
            len(
                {
                    e.document_id
                    for e in review.evidence
                    if e.evidence_id in c.evidence_ids and e.evidence_kind != "citation_relation"
                }
            )
            >= 2
            for c in review.claims
        ),
    )
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "cloud_calls",
                    "evidence_papers",
                    "published_claims",
                    "warnings",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "source_checks_passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--max-evidence", type=int, choices=range(20, 61), default=40)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
