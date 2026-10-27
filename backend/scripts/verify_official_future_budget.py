"""Audit local future/progress anchors across official scopes, without any cloud calls.

Tests budget invariance and literal PDF/AST locations, not whether an idea is
scientifically open or resolved. Sources and prior acceptance results stay intact.
"""

import argparse
import asyncio
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from verify_official_local_features import GRAPH_FILES, compact, configure
from verify_official_output_features import select_sources


async def run(args) -> int:
    fixture = args.fixture.resolve()
    if fixture == TEMP_ROOT or not fixture.is_relative_to(TEMP_ROOT):
        raise ValueError("Requires a previously audited isolated official fixture")
    rows = json.loads((fixture / "fixture.json").read_bytes())
    if len(rows) != 39 or len({r["filename"] for r in rows}) != 39:
        raise ValueError("Requires the complete 39-file official fixture")
    rows = select_sources(fixture, [r["filename"] for r in rows])
    root = isolated_output(args.output_dir)
    configure(root)
    (root / "parsed").mkdir(exist_ok=True)

    import fitz

    from app.core.database import SessionLocal, init_db
    from app.llm.extractive import ExtractiveProvider
    from app.models.document import Document, DocumentStatus
    from app.services.evidence_review import EvidenceReviewService

    init_db()
    parsed = {}
    with SessionLocal() as db:
        docs = {}
        for row in rows:
            doc_id = row["document_id"]
            source = fixture / "parsed" / (doc_id + ".json")
            parsed[doc_id] = ast = json.loads(source.read_bytes())
            shutil.copyfile(source, root / "parsed" / source.name)
            identifier = re.match(r"(\d{4}\.\d{4,5})(?:v(\d+))?", row["filename"])
            doc = Document(
                id=doc_id,
                original_filename=row["filename"],
                storage_key=doc_id + ".pdf",
                sha256=row["source_sha256"],
                size_bytes=1,
                status=DocumentStatus.READY,
                title=ast["title"],
                authors_json=json.dumps(ast.get("authors", [])),
                page_count=len(ast["pages"]),
                arxiv_id=identifier[1] if identifier else None,
            )
            db.add(doc)
            docs[row["filename"]] = doc
        db.commit()
        report = {
            "status": "running",
            "cloud_calls": 0,
            "production_library_modified": False,
            "sources": rows,
            "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
            "scope": (
                "Budget invariance, immutable source fingerprints and literal anchor support; "
                "not scientific relevance or solved-idea proof"
            ),
            "runs": [],
            "source_audit": [],
        }
        write_json(root / "report.json", report)
        failures = []
        audit_pool = {}
        for name, filenames, budgets in [
            ("distinct-20", GRAPH_FILES, [20, 30, 40, 60]),
            ("files-39", [r["filename"] for r in rows], [40, 60]),
        ]:
            prior = None
            for budget in budgets:
                result = await EvidenceReviewService(db, provider=ExtractiveProvider()).generate(
                    [docs[f] for f in filenames], max_evidence=budget
                )
                write_json(root / f"{name}-budget-{budget}.json", result.model_dump(mode="json"))
                local = {e.evidence_id: e for e in result.future_work_evidence}
                state = {d.direction_id: d.model_dump() for d in result.future_directions}
                invariant = prior is None or state == prior
                prior = state
                generation_ids = {e.evidence_id for e in result.evidence}
                links_valid = all(
                    d.source_evidence_id in local and set(d.progress_evidence_ids) <= local.keys()
                    for d in result.future_directions
                )
                counts = Counter(
                    e.document_id for e in local.values() if e.evidence_kind == "future_work"
                )
                check = {
                    "scope": name,
                    "selected_files": len(filenames),
                    "budget": budget,
                    "generation_evidence": len(result.evidence),
                    "local_evidence": len(local),
                    "future_candidates": len(result.future_directions),
                    "candidate_source_files": len(counts),
                    "state_invariant": invariant,
                    "local_direction_links_valid": links_valid,
                    "generation_claims_use_only_sent_evidence": all(
                        set(c.evidence_ids) <= generation_ids for c in result.claims
                    ),
                    "generation_within_budget": len(result.evidence) <= budget,
                    "per_file_candidate_limit": max(counts.values(), default=0) <= 4,
                }
                if not all(
                    check[key]
                    for key in (
                        "state_invariant",
                        "local_direction_links_valid",
                        "generation_claims_use_only_sent_evidence",
                        "generation_within_budget",
                        "per_file_candidate_limit",
                    )
                ):
                    failures.append(f"Budget or source-scope check failed: {name}/{budget}")
                for e in local.values():
                    audit_pool[(e.document_id, e.page_number, tuple(e.block_ids), e.quote)] = e
                report["runs"].append(check)
                write_json(root / "report.json", report)
                print(json.dumps(check), flush=True)

        by_id = {r["document_id"]: r for r in rows}
        native_pages = {}
        for e in audit_pool.values():
            pages = {p["page_number"]: p for p in parsed[e.document_id]["pages"]}
            page = pages.get(e.page_number, {})
            blocks = {b["block_id"]: b for b in page.get("blocks", [])}
            raw = " ".join(
                " ".join(blocks[key]["text"].split()) for key in e.block_ids if key in blocks
            )
            if not e.block_ids and e.source_type == "abstract":
                raw = " ".join(parsed[e.document_id].get("abstract", "").split())
            ast_valid = bool(page) and e.quote in raw
            key = (e.document_id, e.page_number)
            if key not in native_pages:
                with fitz.open(fixture / "uploads" / (e.document_id + ".pdf")) as pdf:
                    native_pages[key] = compact(pdf[e.page_number - 1].get_text())
            native = native_pages[key]
            native_support = bool(native) and compact(e.quote) in native
            audit = {
                "filename": by_id[e.document_id]["filename"],
                "page_number": e.page_number,
                "block_ids": e.block_ids,
                "evidence_kind": e.evidence_kind,
                "ast_quote_supported": ast_valid,
                "native_support": "literal_match"
                if native_support
                else "no_native_layer"
                if not native
                else "needs_inspection",
            }
            if not ast_valid:
                failures.append("AST quote mismatch: " + audit["filename"])
            report["source_audit"].append(audit)
        report["source_files_unchanged"] = all(
            digest(fixture / "uploads" / (r["document_id"] + ".pdf")) == r["source_sha256"]
            and digest(fixture / "parsed" / (r["document_id"] + ".json")) == r["ast_sha256"]
            for r in rows
        )
        if not report["source_files_unchanged"]:
            failures.append("Source fixture changed")
        report["failures"] = failures
        report["native_audit_summary"] = dict(
            Counter(a["native_support"] for a in report["source_audit"])
        )
        report["status"] = "failed" if failures else "budget_and_AST_checks_passed"
        report["scientific_future_directions_certified"] = False
        write_json(root / "report.json", report)
        print(json.dumps({k: report[k] for k in ("status", "native_audit_summary", "failures")}))
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
