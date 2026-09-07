import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # noqa: E402

from app.parsers.diagnostics import inspect_pdf  # noqa: E402
from app.parsers.registry import get_parser  # noqa: E402


def evaluate_pdf(path: Path, expectation: dict[str, Any] | None = None) -> dict[str, Any]:
    expectation = expectation or {}
    with fitz.open(path) as document:
        page_count = len(document)
        native_text_chars = sum(len(page.get_text("text")) for page in document)
    diagnostics = inspect_pdf(str(path))
    parser = get_parser(str(path))
    tracemalloc.start()
    started = time.perf_counter()
    parsed = parser.parse(str(path))
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    metrics = {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "page_count": page_count,
        "native_text_chars": native_text_chars,
        "extracted_text_chars": len(parsed.full_text),
        "content_kind": diagnostics.content_kind.value,
        "parser": parser.name,
        "elapsed_seconds": round(elapsed, 3),
        "python_peak_memory_mb": round(peak / 1024 / 1024, 2),
        "title": parsed.title,
        "authors": len(parsed.authors),
        "outline_nodes": _count_outline(parsed.outline),
        "tables": len(parsed.tables),
        "figures": len(parsed.figures),
        "formulas": len(parsed.formulas),
        "references": len(parsed.references),
        "warnings": parsed.warnings,
    }
    failures = _validate(metrics, expectation)
    return {**metrics, "status": "passed" if not failures else "failed", "failures": failures}


def _validate(metrics: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures = []
    minimums = {
        "min_pages": "page_count",
        "min_text_chars": "native_text_chars",
        "min_extracted_text_chars": "extracted_text_chars",
        "min_outline_nodes": "outline_nodes",
        "min_tables": "tables",
        "min_figures": "figures",
        "min_formulas": "formulas",
        "min_references": "references",
    }
    for expectation_key, metric_key in minimums.items():
        minimum = expected.get(expectation_key)
        if minimum is not None and metrics[metric_key] < minimum:
            failures.append(f"{metric_key}={metrics[metric_key]} < {minimum}")
    maximums = {
        "max_elapsed_seconds": "elapsed_seconds",
        "max_python_peak_memory_mb": "python_peak_memory_mb",
    }
    for expectation_key, metric_key in maximums.items():
        maximum = expected.get(expectation_key)
        if maximum is not None and metrics[metric_key] > maximum:
            failures.append(f"{metric_key}={metrics[metric_key]} > {maximum}")
    content_kind = expected.get("content_kind")
    if content_kind and metrics["content_kind"] != content_kind:
        failures.append(f"content_kind={metrics['content_kind']} != {content_kind}")
    title_contains = expected.get("title_contains")
    if title_contains and title_contains.casefold() not in (metrics["title"] or "").casefold():
        failures.append(f"title does not contain {title_contains!r}")
    return failures


def _count_outline(nodes: list[Any]) -> int:
    return sum(1 + _count_outline(node.children) for node in nodes)


def load_expectations(manifest_path: Path | None) -> dict[str, dict[str, Any]]:
    if manifest_path is None:
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = [*manifest.get("downloads", []), *manifest.get("local_fixtures", [])]
    return {item["filename"]: item.get("expect", {}) for item in items}


def evaluate_pdf_safely(
    path: Path, expectation: dict[str, Any] | None = None
) -> dict[str, Any]:
    try:
        return evaluate_pdf(path, expectation)
    except Exception as exc:
        return {
            "filename": path.name,
            "status": "failed",
            "failures": [f"{type(exc).__name__}: {exc}"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the PDF parser on a local corpus.")
    parser.add_argument("corpus_dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    expectations = load_expectations(args.manifest)
    pdfs = sorted(args.corpus_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDF files found in {args.corpus_dir}")
    results = [evaluate_pdf_safely(path, expectations.get(path.name)) for path in pdfs]
    report = {
        "schema_version": "1.0",
        "generated_at_unix": int(time.time()),
        "summary": {
            "total": len(results),
            "passed": sum(item["status"] == "passed" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
        },
        "documents": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.strict and report["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
