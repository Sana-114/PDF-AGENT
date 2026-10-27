"""Fresh isolated 20-paper synthesis with independent future-work evidence pool.

No production writes, source reparsing or automatic generation retries. The
semantic quality of the generated prose must be reviewed independently.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import digest, isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_local_features import GRAPH_FILES, seed
from verify_official_output_features import configure


async def run(args):
    if not args.allow_cloud:
        raise ValueError("Explicit authorization for public excerpts required")
    inputs = saved_inputs(args.fixture, GRAPH_FILES)
    root = isolated_output(args.output_dir)
    configure(root)
    from app.core.database import SessionLocal
    from app.llm.factory import get_llm_provider
    from app.models.document import Document
    from app.services.evidence_review import EvidenceReviewService

    indexed = seed(root, inputs)
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Actual DeepSeek required")
    calls = []
    original = provider._post_response

    async def record(payload):
        row = {"request": payload, "status": "started"}
        calls.append(row)
        write_json(root / "calls.json", calls)
        try:
            result = await original(payload)
            row.update(status="returned", response=result)
            return result
        except Exception as exc:
            row.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(root / "calls.json", calls)

    provider._post_response = record
    report = {
        "status": "running",
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "production_library_modified": False,
        "automatic_retries": 0,
        "source_fixture_reused_not_reparsed": True,
        "cloud_calls": 0,
        "sources": json.loads((root / "fixture.json").read_bytes()),
    }
    write_json(root / "report.json", report)
    try:
        with SessionLocal() as db:
            docs = [db.get(Document, key) for key in indexed]
            result = await EvidenceReviewService(db, provider=provider).generate(
                docs, max_evidence=40
            )
        write_json(root / "review.json", result.model_dump(mode="json"))
        anchors = {e.evidence_id: e for e in result.evidence}
        checks = {
            "actual_deepseek": result.provider == "deepseek" and len(calls) == 2,
            "semantic_review_completed": result.semantic_review is not None
            and result.semantic_review.status == "checked",
            "twenty_papers_in_pool": len({e.document_id for e in result.evidence}) == 20,
            "claims_have_valid_ids": bool(result.claims)
            and all(
                c.evidence_ids and set(c.evidence_ids) <= anchors.keys() for c in result.claims
            ),
            "short_review": 0 < len(result.claims) <= 8 and len(result.review) <= 1600,
            "at_least_two_cross_paper_claims": sum(
                len(
                    {
                        anchors[key].document_id
                        for key in c.evidence_ids
                        if anchors[key].evidence_kind != "citation_relation"
                    }
                )
                >= 2
                for c in result.claims
            )
            >= 2,
            "themes_with_published_indices": bool(result.themes)
            and all(
                all(0 <= i < len(result.claims) for i in t.claim_indices) for t in result.themes
            ),
            "source_copies_unchanged": all(
                digest(root / "uploads" / (r["document_id"] + ".pdf")) == r["source_sha256"]
                and digest(root / "parsed" / (r["document_id"] + ".json")) == r["ast_sha256"]
                for r in report["sources"]
            ),
        }
        report.update(
            status="checks_passed_pending_semantic_review" if all(checks.values()) else "held",
            checks=checks,
            published_claims=len(result.claims),
            future_directions=len(result.future_directions),
            warnings=result.warnings,
            scientific_review="pending",
        )
    except Exception as exc:
        report.update(status="error", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
    report["cloud_calls"] = len(calls)
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("sources", "runtime_source")},
            ensure_ascii=False,
        )
    )
    return int(report["status"] not in {"checks_passed_pending_semantic_review"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
