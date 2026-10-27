"""Source-pinned CSV, writing and live discovery acceptance in fresh isolated storage.

Failures and raw cloud responses stay local. No automatic generation retries,
no production database writes, and no changes to the official source PDFs.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from statistics import stdev

from inspect_runtime_source import inspect_source
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json


def configure(root: Path) -> None:
    for key, value in {
        "DATABASE_URL": f"sqlite:///{(root / 'acceptance.db').as_posix()}",
        "DATA_DIR": str(root),
        "UPLOAD_DIR": str(root / "uploads"),
        "PARSED_DIR": str(root / "parsed"),
        "TRANSLATION_DIR": str(root / "translations"),
        "VECTOR_SEARCH_ENABLED": "false",
        "GITHUB_CODE_LOOKUP_LIMIT": "1",
    }.items():
        os.environ[key] = value


def select_sources(fixture: Path, names: list[str]) -> list[dict]:
    fixture = fixture.resolve()
    if not fixture.is_relative_to(TEMP_ROOT) or fixture == TEMP_ROOT:
        raise ValueError("Use only the known isolated official fixture")
    rows = json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))
    selected = [next(row for row in rows if row["filename"] == name) for name in names]
    for row in selected:
        for kind, suffix, field in (
            ("uploads", ".pdf", "source_sha256"),
            ("parsed", ".json", "ast_sha256"),
        ):
            if digest(fixture / kind / (row["document_id"] + suffix)) != row[field]:
                raise ValueError("Official source/AST identity changed")
    return selected


def charts(args, root: Path) -> dict:
    import ast

    import fitz
    from fastapi.testclient import TestClient

    from app.main import app

    rows = select_sources(args.fixture, ["1706.03762v1.pdf"])
    row = rows[0]
    source = args.fixture / "uploads" / (row["document_id"] + ".pdf")
    with fitz.open(source) as native:
        page = native[8].get_text()
    # Independently transcribed Table 3, rows (C), changing number of layers.
    # The complete physical page is also rendered and manually reviewed.
    required = [
        "Table 3:",
        "newstest2013",
        "6.11",
        "23.7",
        "36",
        "5.19",
        "25.3",
        "50",
        "4.88",
        "25.5",
        "80",
    ]
    if any(token not in page for token in required):
        raise ValueError("The independently selected native table values are absent")
    data = (
        b"layers,PPL_wordpiece,BLEU_dev,parameters_million\n"
        b"2,6.11,23.7,36\n4,5.19,25.3,50\n8,4.88,25.5,80\n"
    )
    inputs = {
        "table3-line": (
            data,
            {"chart_type": "line", "x_column": "layers", "y_columns": "BLEU_dev"},
        ),
        "table3-bar": (data, {"chart_type": "bar", "x_column": "layers", "y_columns": "BLEU_dev"}),
        "table3-radar": (
            b"configuration,PPL_wordpiece,BLEU_dev,parameters_million\n"
            b"2 layers,6.11,23.7,36\n4 layers,5.19,25.3,50\n8 layers,4.88,25.5,80\n",
            {"chart_type": "radar3d"},
        ),
        "synthetic-missing-duplicate": (
            b"model,accuracy,latency_ms,memory_MB\nA,0.8,10,100\nA,,20,100\nC,0.9,15,100\n",
            {"chart_type": "radar3d"},
        ),
        "synthetic-unsorted-dates": (
            b"date,score\n2026-03-01,3\n2026-01-01,1\n2026-02-01,2\n",
            {"chart_type": "line"},
        ),
        "synthetic-replicates": (
            b"dataset,model,score\nD1,A,0\nD1,A,2\nD1,B,\nD1,B,4\nD2,A,3\nD2,A,5\n",
            {
                "chart_type": "bar",
                "x_column": "dataset",
                "y_columns": "score",
                "group_column": "model",
                "aggregation": "mean",
                "error_mode": "sem",
            },
        ),
    }
    results = []
    with TestClient(app) as client:
        for name, (content, form) in inputs.items():
            (root / (name + ".csv")).write_bytes(content)
            response = client.post(
                "/api/v1/visualizations/analyze",
                files={"file": (name + ".csv", content, "text/csv")},
                data=form,
            )
            payload = response.json()
            write_json(root / (name + ".json"), payload)
            checks = {"api_200": response.status_code == 200}
            if response.status_code == 200:
                chart = payload["chart"]
                script = chart["matplotlib_script"]
                (root / (name + ".py")).write_text(script, encoding="utf-8")
                tree = ast.parse(script)
                assigned = {
                    node.targets[0].id: ast.literal_eval(node.value)
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in {"series", "categories", "errors"}
                }
                checks["preview_raw_values"] = payload["preview_rows"][0] != {}
                checks["source_sha"] = payload["sha256"] == hashlib.sha256(content).hexdigest()
                checks["export_series_count"] = len(assigned["series"]) == len(chart["series"])
                if name in {"table3-line", "table3-bar"}:
                    checks["independent_numbers"] = chart["series"][0]["values"] == [
                        23.7,
                        25.3,
                        25.5,
                    ]
                    checks["caption_extrema"] = (
                        "23.7" in chart["caption"] and "25.5" in chart["caption"]
                    )
                    if name == "table3-line":
                        checks["true_numeric_x_spacing"] = chart["x_coordinates"] == [2, 4, 8]
                elif name == "table3-radar":
                    expected = [
                        [1, 0, 0],
                        [(5.19 - 4.88) / (6.11 - 4.88), (25.3 - 23.7) / (25.5 - 23.7), 14 / 44],
                        [0, 1, 1],
                    ]
                    checks["independent_normalization"] = all(
                        math.isclose(a, b, abs_tol=1e-12)
                        for series, values in zip(chart["series"], expected, strict=True)
                        for a, b in zip(series["values"], values, strict=True)
                    )
                    checks["normalization_disclosed"] = "min-max" in chart["caption"]
                elif name == "synthetic-missing-duplicate":
                    checks["missing_retained_api"] = chart["series"][1]["values"][0] is None
                    checks["missing_not_zero_export"] = (
                        "0 if value is None else value" not in script
                    )
                    checks["constant_disclosed"] = "常量" in chart["normalization"]
                elif name == "synthetic-unsorted-dates":
                    checks["chronological"] = chart["categories"] == [
                        "2026-01-01",
                        "2026-02-01",
                        "2026-03-01",
                    ]
                    checks["paired_values"] = chart["series"][0]["values"] == [1, 2, 3]
                else:
                    first = chart["series"][0]
                    checks["means_and_zero"] = first["values"] == [1, 4]
                    checks["sem"] = first["errors"] == [stdev([0, 2]) / math.sqrt(2), 1]
                    checks["missing_not_zero"] = chart["series"][1]["values"] == [4, None]
                    checks["single_sample_uncertainty"] = chart["series"][1]["errors"][0] is None
            results.append(
                {
                    "case": name,
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks,
                }
            )
            write_json(root / "report.json", {"status": "running", "cases": results})
    return {
        "status": "passed" if all(x["status"] == "passed" for x in results) else "failed",
        "mode": "charts",
        "sources": rows,
        "physical_page": 9,
        "source_table": "Table 3 (C)",
        "scope": "Real API and exported script data; rendering inspected separately",
        "cloud_calls": 0,
        "cases": results,
    }


async def discovery(args, root: Path) -> dict:
    from app.core.config import settings
    from app.core.database import SessionLocal, init_db
    from app.models.recommendation import ArxivSubscription
    from app.services.paper_discovery import PaperDiscoveryService
    from app.services.recommendations import RecommendationService

    calls = []

    class RecordingDiscovery(PaperDiscoveryService):
        async def _get(self, url, *, provider, params=None):
            record = {"provider": provider, "url": url, "params": params, "status": "started"}
            calls.append(record)
            try:
                response = await super()._get(url, provider=provider, params=params)
                record.update(status="returned", http_status=response.status_code)
                (root / f"response-{len(calls):03d}.txt").write_text(
                    response.text, encoding="utf-8"
                )
                return response
            except Exception as exc:
                record.update(status="failed", error=str(exc))
                raise
            finally:
                write_json(root / "network-calls.json", calls)

    service = RecordingDiscovery(settings)
    results = []
    for query, title, identifier in [
        (
            "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale",
            "An Image is Worth 16x16 Words",
            "2010.11929",
        ),
        ("arxiv:2106.09685v2", "LoRA: Low-Rank Adaptation", "2106.09685"),
    ]:
        try:
            kind, items, warnings = await service.search(query, 3)
            write_json(
                root / f"search-{len(results) + 1}.json", [x.model_dump(mode="json") for x in items]
            )
            found = next((x for x in items if title.casefold() in x.title.casefold()), None)
            checks = {
                "correct_title": found is not None,
                "correct_identity": found is not None and found.arxiv_id == identifier,
            }
            if "v2" in query:
                checks["exact_requested_version"] = found is not None and found.arxiv_version == 2
            results.append(
                {
                    "case": query,
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks,
                    "warnings": warnings,
                }
            )
            if found and found.importable and all(checks.values()):
                staged = await service.download(found)
                destination = staged.commit()
                import fitz

                with fitz.open(destination) as pdf:
                    checks["download_title_native_page1"] = (
                        title.casefold() in " ".join(pdf[0].get_text().split()).casefold()
                    )
                    checks["download_pages_nonempty"] = len(pdf) > 0
                results[-1]["download"] = {
                    "sha256": digest(destination),
                    "bytes": destination.stat().st_size,
                    "source_id": found.source_id,
                    "pdf_url": found.pdf_url,
                }
                results[-1]["status"] = "passed" if all(checks.values()) else "failed"
        except Exception as exc:
            results.append({"case": query, "status": "failed", "error": str(exc)})
        write_json(root / "report.json", {"status": "running", "cases": results})
        # arXiv asks clients to space requests. No retries on failed cases.
        await asyncio.sleep(settings.arxiv_request_delay_seconds)
    init_db()
    with SessionLocal() as db:
        subscription = ArxivSubscription(
            name="Isolated acceptance", query="low-rank adaptation", max_results=3, active=False
        )
        db.add(subscription)
        db.commit()
        summary = await RecommendationService(discovery=service).refresh(db, subscription.id)
        results.append(
            {
                "case": "live-arxiv-refresh",
                "status": "passed"
                if summary.error is None and summary.discovered > 0
                else "failed",
                **asdict(summary),
            }
        )
    return {
        "status": "passed" if all(x["status"] == "passed" for x in results) else "partial",
        "mode": "discovery",
        "cases": results,
        "network_metadata_calls": len(calls),
        "cloud_calls": 0,
        "scope": (
            "Live provider search/download and one inactive isolated subscription refresh; "
            "no scheduler or production import claim"
        ),
    }


async def writing(args, root: Path) -> dict:
    if not args.allow_cloud:
        raise ValueError("Writing requires explicit --allow-cloud")
    import fitz

    from app.agent.harness import ResearchAgent
    from app.core.database import SessionLocal, init_db
    from app.llm.factory import get_llm_provider
    from app.models.document import Document, DocumentStatus
    from app.services.chunking import replace_document_chunks
    from app.services.writing_outline import WritingOutlineService

    filenames = getattr(args, "writing_files", None) or [
        "2010.11929v2.pdf",
        "2103.00020v1.pdf",
        "2106.09685v2.pdf",
    ]
    rows = select_sources(args.fixture, list(dict.fromkeys(filenames)))
    init_db()
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Requires real DeepSeek, not an offline substitute")
    calls = []
    original = provider._post_response

    async def recorded(payload):
        record = {"request": payload, "status": "started"}
        calls.append(record)
        write_json(root / "calls.json", calls)
        try:
            raw = await original(payload)
            record.update(status="returned", response=raw)
            return raw
        except Exception as exc:
            record.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(root / "calls.json", calls)

    provider._post_response = recorded
    native_front = {}
    with SessionLocal() as db:
        documents = []
        for row in rows:
            doc_id = row["document_id"]
            source = args.fixture / "uploads" / (doc_id + ".pdf")
            ast_path = args.fixture / "parsed" / (doc_id + ".json")
            parsed = json.loads(ast_path.read_text(encoding="utf-8"))
            for kind, suffix in (("uploads", ".pdf"), ("parsed", ".json")):
                (root / kind).mkdir(exist_ok=True)
                shutil.copyfile(
                    args.fixture / kind / (doc_id + suffix), root / kind / (doc_id + suffix)
                )
            document = Document(
                id=doc_id,
                original_filename=row["filename"],
                storage_key=doc_id + ".pdf",
                sha256=row["source_sha256"],
                size_bytes=source.stat().st_size,
                title=parsed["title"],
                authors_json=json.dumps(parsed["authors"]),
                page_count=len(parsed["pages"]),
                status=DocumentStatus.READY,
                arxiv_id=row["filename"].split("v")[0],
            )
            db.add(document)
            db.flush()
            replace_document_chunks(db, doc_id, parsed)
            documents.append(document)
            with fitz.open(source) as native:
                native_front[doc_id] = " ".join(
                    (native[0].get_text() + native[1].get_text()).split()
                ).casefold()
        db.commit()
        result = await WritingOutlineService(
            db, agent=ResearchAgent(db, provider=provider)
        ).generate(
            getattr(args, "writing_idea", None)
            or (
                "Investigate parameter-efficient adaptation of visual representations: "
                "vision transformers, contrastive image-text pretraining, and low-rank adaptation. "
                "No new experiments have been conducted."
            ),
            documents,
            top_k=15,
            language="en",
        )
    write_json(root / "writing-outline.json", result.model_dump(mode="json"))
    evidence_ids = {x.evidence_id for x in result.evidence}
    claimed_ids = {x for claim in result.claims for x in claim.evidence_ids}
    coverage = {x.document_id for x in result.evidence if x.evidence_id in claimed_ids}
    reference_checks = []
    for ref in result.references:

        def normalize(value):
            return re.sub(r"[^a-z0-9]", "", value.casefold())

        native = normalize(native_front[ref.document_id])
        checks = {
            "title_native_front": normalize(ref.title) in native,
            "authors_present": bool(ref.authors),
            "all_authors_native_front": bool(ref.authors)
            and all(normalize(a) in native for a in ref.authors),
            "year_present": ref.publication_year is not None,
            "evidence_ids_valid": bool(ref.evidence_ids) and set(ref.evidence_ids) <= evidence_ids,
        }
        reference_checks.append(
            {"citation_key": ref.citation_key, "document_id": ref.document_id, "checks": checks}
        )
    checks = {
        "real_generation": result.provider == "deepseek" and len(calls) == 1,
        "all_selected_papers_claimed": len(coverage) == len(rows),
        "grounded_claims": bool(result.claims)
        and all(c.evidence_ids and set(c.evidence_ids) <= evidence_ids for c in result.claims),
        "three_sections": [s.section_id for s in result.sections]
        == ["abstract", "introduction", "related_work"],
        "bibliography_independent_front_pages": len(reference_checks) == len(rows)
        and all(all(x["checks"].values()) for x in reference_checks),
        "not_insufficient": not result.insufficient_evidence,
    }
    return {
        "status": "source_checks_passed" if all(checks.values()) else "failed",
        "mode": "writing",
        "idea": result.idea,
        "sources": rows,
        "checks": checks,
        "references": reference_checks,
        "cloud_calls": len(calls),
        "automatic_retries": 0,
        "claims": len(result.claims),
        "evidence": len(result.evidence),
        "claimed_papers": len(coverage),
        "warnings": result.warnings,
        "semantic_review": "pending",
        "retrieval_scope": "lexical + configured reranker, not BGE vector acceptance",
    }


async def run(args) -> int:
    root = isolated_output(args.output_dir)
    configure(root)
    report = {
        "status": "running",
        "mode": args.mode,
        "automatic_retries": 0,
        "production_library_modified": False,
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
    }
    write_json(root / "report.json", report)
    try:
        report.update(
            charts(args, root) if args.mode == "charts" else await globals()[args.mode](args, root)
        )
    except Exception as exc:
        report.update(status="error", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in {"passed", "source_checks_passed"} else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["charts", "writing", "discovery"], required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument(
        "--writing-idea", help="Fixed, recorded writing question for a scoped check"
    )
    parser.add_argument(
        "--writing-files",
        nargs="+",
        choices=["2010.11929v2.pdf", "2103.00020v1.pdf", "2106.09685v2.pdf"],
        help="Select only previously audited public writing sources; defaults to all three",
    )
    raise SystemExit(asyncio.run(run(parser.parse_args())))
