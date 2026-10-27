"""Reparse declared official fixture copies into a new, isolated candidate library.

Reuse the existing seed/identity helpers. No model calls or running-library writes.
Table-count changes are audit findings, not automatic accuracy scores.
"""

import argparse
import json
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import PROJECT, TEMP_ROOT, digest, isolated_output, write_json
from verify_official_local_features import GRAPH_FILES, configure, seed
from verify_official_output_features import select_sources


def run(args):
    fixture = args.fixture.resolve()
    if fixture == TEMP_ROOT or not fixture.is_relative_to(TEMP_ROOT):
        raise ValueError("Only existing isolated official fixtures may be reparsed")
    rows = select_sources(fixture, args.files or GRAPH_FILES)
    root = isolated_output(args.output_dir)
    configure(root)
    from app.parsers.pymupdf_parser import PyMuPDFParser

    parser = PyMuPDFParser()
    report = {
        "status": "running", "cloud_calls": 0, "production_library_modified": False,
        "parser_version": parser.version, "runtime_source": inspect_source(PROJECT / "backend"),
        "scope": "Fresh native parsing and table-boundary audit, not full table accuracy",
        "documents": [],
    }
    write_json(root / "report.json", report)
    inputs = []
    for row in rows:
        name, document_id = row["filename"], row["document_id"]
        source = fixture / "uploads" / (document_id + ".pdf")
        old = json.loads((fixture / "parsed" / (document_id + ".json")).read_bytes())
        parsed = parser.parse(str(source), batch_size=20).to_dict()
        ast = root / "fresh-ast" / (document_id + ".json")
        write_json(ast, parsed)
        basic = (
            digest(source) == row["source_sha256"]
            and len(parsed["pages"]) == len(old["pages"])
            and [p["page_number"] for p in parsed["pages"]]
            == list(range(1, len(old["pages"]) + 1))
        )
        old_captions = {(t["page_number"], t.get("caption")) for t in old.get("tables", [])}
        new_captions = {(t["page_number"], t.get("caption")) for t in parsed.get("tables", [])}
        report["documents"].append({
            **row, "candidate_ast_sha256": digest(ast), "page_count": len(parsed["pages"]),
            "basic_checks_passed": basic, "old_tables": len(old.get("tables", [])),
            "new_tables": len(parsed.get("tables", [])),
            "previous_captions_not_found": sorted(old_captions - new_captions, key=str),
            "new_captions": sorted(new_captions - old_captions, key=str),
        })
        inputs.append({
            "row": {"filename": name, "source_sha256": row["source_sha256"],
                    "size_bytes": source.stat().st_size, "title": parsed["title"],
                    "author_names": parsed["authors"], "page_count": len(parsed["pages"]),
                    "parser": parser.name, "parser_version": parser.version},
            "source": source, "ast": ast, "ast_sha256": digest(ast), "parsed": parsed,
        })
        write_json(root / "report.json", report)
        print(json.dumps({"file": name, "basic": basic,
                          "old_tables": report["documents"][-1]["old_tables"],
                          "new_tables": report["documents"][-1]["new_tables"]}), flush=True)
    seed(root, inputs)
    # Seed SQL chunks from the new AST, never carry stale chunks from the old fixture.
    from app.core.database import SessionLocal
    from app.services.chunking import replace_document_chunks

    with SessionLocal() as session:
        for item in inputs:
            replace_document_chunks(session, item["document_id"], item["parsed"])
        session.commit()
    report["status"] = "basic_checks_passed" if all(
        r["basic_checks_passed"] for r in report["documents"]
    ) else "failed"
    report["selected_files"] = len(rows)
    report["candidate_SQL_chunks_rebuilt"] = True
    report["full_table_quality"] = "pending_independent_page_review"
    write_json(root / "report.json", report)
    return 0 if report["status"] == "basic_checks_passed" else 1


def check_table_rows(args):
    """Check saved candidate tables against independently transcribed rows.

    This does not reparse PDFs, change candidate ASTs, or call a model. Gold
    values belong only to eval assets, never to the production parser.
    """
    fixture = args.fixture.resolve()
    gold = json.loads(args.table_expectations.read_bytes())
    names = list(dict.fromkeys(case["filename"]
                              for case in [*gold["cases"], *gold.get("negative_cases", [])]))
    rows = {r["filename"]: r for r in select_sources(fixture, names)}
    root = isolated_output(args.output_dir)
    results = []
    for case in gold["cases"]:
        row = rows[case["filename"]]
        parsed = json.loads((fixture / "parsed" / (row["document_id"] + ".json")).read_bytes())
        tables = [t for t in parsed.get("tables", []) if t["page_number"] == case["page"]
                  and (t.get("caption") or "").startswith(case["caption_prefix"])]
        checks = {"original_pdf_identity": row["source_sha256"] == case["source_sha256"],
                  "one_table_per_caption": len(tables) == 1}
        data_rows = []
        if len(tables) == 1:
            table = tables[0]
            block = next((b for p in parsed["pages"] if p["page_number"] == case["page"]
                          for b in p["blocks"] if b["block_id"] == table["caption_block_id"]), None)
            checks["caption_anchor"] = bool(block and block["text"] == table["caption"])
            checks["caption_geometry"] = bool(block and (
                table["bbox"][3] < block["bbox"][1] if case["caption_below"]
                else table["bbox"][1] > block["bbox"][3]
            ))
            for expected in case["rows"]:
                matches = [r for r in table["rows"] if r and r[0] == expected[0]]
                actual = None
                if len(matches) == 1:
                    values = matches[0]
                    columns = case.get("value_columns", list(range(1, len(values))))
                    if all(0 <= c < len(values) for c in columns):
                        actual = [values[c] for c in columns]
                data_rows.append({"label": expected[0], "expected": expected[1:],
                                  "actual": actual, "passed": actual == expected[1:]})
        checks["independent_rows"] = len(data_rows) == len(case["rows"]) and all(
            r["passed"] for r in data_rows
        )
        results.append({"filename": case["filename"], "page": case["page"],
                        "caption": case["caption_prefix"], "checks": checks, "rows": data_rows,
                        "status": "passed" if all(checks.values()) else "failed"})
    negatives = []
    for case in gold.get("negative_cases", []):
        row = rows[case["filename"]]
        parsed = json.loads((fixture / "parsed" / (row["document_id"] + ".json")).read_bytes())
        tables = [t for t in parsed.get("tables", []) if t["page_number"] == case["page"]]
        figures = [f for f in parsed.get("figures", []) if f["page_number"] == case["page"]
                   and (f.get("caption") or "").startswith(case["figure_caption_prefix"])]
        checks = {"original_pdf_identity": row["source_sha256"] == case["source_sha256"],
                  "figure_identity": len(figures) == 1}
        if case.get("no_tables_on_page"):
            checks["no_table_on_chart_only_page"] = not tables
        region = case.get("excluded_table_region")
        if region:
            checks["no_table_in_plot_region"] = not any(
                min(t["bbox"][2], region[2]) > max(t["bbox"][0], region[0])
                and min(t["bbox"][3], region[3]) > max(t["bbox"][1], region[1]) for t in tables
            )
        expected = case["figure_region"]
        checks["visible_figure_bounds"] = len(figures) == 1 and all(
            expected[0] <= f["bbox"][0] <= f["bbox"][2] <= expected[2]
            and expected[1] <= f["bbox"][1] <= f["bbox"][3] <= expected[3] for f in figures
        )
        negatives.append({"filename": case["filename"], "page": case["page"],
                          "checks": checks,
                          "actual_figure_bboxes": [f["bbox"] for f in figures],
                          "status": "passed" if all(checks.values()) else "failed"})
    report = {"status": "passed" if all(r["status"] == "passed"
                                        for r in [*results, *negatives]) else "failed",
              "scope": gold["scope"], "cloud_calls": 0, "production_library_modified": False,
              "expectations_sha256": digest(args.table_expectations),
              "sources": list(rows.values()),
              "cases": results, "negative_cases": negatives}
    write_json(root / "report.json", report)
    print(json.dumps({"status": report["status"], "cases": len(results),
                      "passed": sum(r["status"] == "passed" for r in results)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--fixture", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--files", nargs="+", choices=GRAPH_FILES)
    cli.add_argument("--table-expectations", type=Path,
                     help="Check saved tables only against independent eval rows; do not reparse")
    args = cli.parse_args()
    raise SystemExit(check_table_rows(args) if args.table_expectations else run(args))
