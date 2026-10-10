"""Strict scan metadata plus separate table-content diagnostics; zero LLM calls."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsers.registry import get_parser  # noqa: E402
from scripts.evaluate_pdf_corpus import _count_outline, _validate, _validate_structure  # noqa: E402

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pdf", type=Path, required=True)
    cli.add_argument("--manifest", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists():
        raise ValueError("Existing reports cannot be overwritten")
    manifest = json.loads(args.manifest.read_bytes())
    entry = next(item for item in manifest["downloads"] if item["filename"] == args.pdf.name)
    sha = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    if sha != entry["sha256"]:
        raise ValueError("Official scan SHA mismatch")
    parser = get_parser(str(args.pdf))
    started = perf_counter()
    parsed = parser.parse(str(args.pdf), batch_size=25)
    elapsed = perf_counter() - started
    processing = parser.processing_metadata()
    metrics = {
        "page_count": len(parsed.pages), "extracted_text_chars": len(parsed.full_text),
        "content_kind": "scanned_image" if processing["ocr_page_count"] == len(parsed.pages)
        else "hybrid", "authors": len(parsed.authors), "title": parsed.title,
        "outline_nodes": _count_outline(parsed.outline), "references": len(parsed.references),
        "ocr_page_count": processing["ocr_page_count"], "elapsed_seconds": round(elapsed, 3),
    }
    # This focused run does not measure Python allocation peak; do not fake that metric.
    expectations = {key: value for key, value in entry["expect"].items()
                    if key != "max_python_peak_memory_mb"}
    failures = [*_validate(metrics, expectations), *_validate_structure(parsed, expectations)]
    table2 = [table for table in parsed.tables if table.caption and
              table.caption.casefold().startswith("table 2")]
    targets = [("big", "28.4"), ("ConvS2S Ensemble", "26.36")]
    checks = {name: any(any(name.casefold() in " ".join(row).casefold() and value in row
                            for row in table.rows) for table in table2) for name, value in targets}
    failures.extend(f"Table 2 row value missing: {name}" for name, ok in checks.items() if not ok)
    report = {
        "status": "failed" if failures else "passed", "llm_calls": 0,
        "source_sha256": sha, "parser_version": parser.version, "metrics": metrics,
        "author_names": parsed.authors, "failures": failures,
        "table_candidate_count": len(parsed.tables), "table2_exact_row_values": checks,
        "table_quality_scope": "Selected BLEU row values only; not all cells or formulas verified",
        "tables": [{"caption": table.caption, "page": table.page_number, "bbox": table.bbox,
                    "rows": table.rows} for table in parsed.tables],
        "warnings": parsed.warnings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("status", "table_candidate_count", "table2_exact_row_values", "failures")},
                     ensure_ascii=False))
    if failures:
        raise SystemExit(1)
