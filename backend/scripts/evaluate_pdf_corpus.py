import argparse
import hashlib
import json
import math
import re
import sys
import tempfile
import time
import tracemalloc
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # noqa: E402

from app.parsers.base import ParsedDocument  # noqa: E402
from app.parsers.diagnostics import inspect_pdf  # noqa: E402
from app.parsers.registry import get_parser  # noqa: E402


def evaluate_pdf(
    path: Path,
    expectation: dict[str, Any] | None = None,
    *,
    batch_pages: int | None = None,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    expectation = expectation or {}
    with path.open("rb") as stream:
        source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    if artifacts_dir is not None:
        artifacts_dir.mkdir(parents=True, exist_ok=False)
    with fitz.open(path) as document:
        page_count = len(document)
        native_text_chars = sum(len(page.get_text("text")) for page in document)
    diagnostics = inspect_pdf(str(path))
    parser = get_parser(str(path))
    progress_events: list[tuple[int, int]] = []
    checkpoint_batches = 0
    tracemalloc.start()
    started = time.perf_counter()
    if artifacts_dir is not None:
        checkpoint_dir = artifacts_dir / "checkpoints"
        try:
            parsed = parser.parse(
                str(path), checkpoint_dir=checkpoint_dir,
                batch_size=batch_pages or 25, source_fingerprint=source_sha256,
                progress_callback=lambda complete, total: progress_events.append((complete, total)),
            )
            checkpoint_batches = len(list(checkpoint_dir.glob("batch-*.json")))
        except Exception:
            tracemalloc.stop()
            raise
    elif batch_pages:
        with tempfile.TemporaryDirectory(prefix="paperpilot-checkpoint-") as directory:
            checkpoint_dir = Path(directory)
            parsed = parser.parse(
                str(path),
                checkpoint_dir=checkpoint_dir,
                batch_size=batch_pages,
                progress_callback=lambda complete, total: progress_events.append((complete, total)),
            )
            checkpoint_batches = len(list(checkpoint_dir.glob("batch-*.json")))
    else:
        parsed = parser.parse(str(path))
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    processing_metadata = getattr(parser, "processing_metadata", None)
    processing = processing_metadata() if callable(processing_metadata) else {}
    metrics = {
        "filename": path.name,
        "source_sha256": source_sha256,
        "size_bytes": path.stat().st_size,
        "page_count": page_count,
        "native_text_chars": native_text_chars,
        "extracted_text_chars": len(parsed.full_text),
        "parsed_page_count": len(parsed.pages),
        "content_kind": diagnostics.content_kind.value,
        "parser": parser.name,
        "elapsed_seconds": round(elapsed, 3),
        "python_peak_memory_mb": round(peak / 1024 / 1024, 2),
        "batch_size_pages": batch_pages,
        "checkpoint_batches": checkpoint_batches,
        "checkpoint_progress_events": len(progress_events),
        "title": parsed.title,
        "authors": len(parsed.authors),
        "author_names": parsed.authors,
        "affiliations": parsed.affiliations,
        "outline_nodes": _count_outline(parsed.outline),
        "tables": len(parsed.tables),
        "figures": len(parsed.figures),
        "formulas": len(parsed.formulas),
        "references": len(parsed.references),
        "ocr_page_count": int(processing.get("ocr_page_count", 0)),
        "ocr_pages": processing.get("ocr_pages", []),
        "warnings": parsed.warnings,
    }
    failures = [*_validate(metrics, expectation), *_validate_structure(parsed, expectation)]
    if [page.page_number for page in parsed.pages] != list(range(1, page_count + 1)):
        failures.append("parsed page sequence does not cover every source page exactly once")
    if artifacts_dir is not None:
        (artifacts_dir / "parsed.json").write_text(
            json.dumps(parsed.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8",
        )
    return {**metrics, "status": "passed" if not failures else "failed", "failures": failures}


def _validate(metrics: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures = []
    exact_pages = expected.get("exact_pages")
    if exact_pages is not None and metrics["page_count"] != exact_pages:
        failures.append(f"page_count={metrics['page_count']} != {exact_pages}")
    minimums = {
        "min_pages": "page_count",
        "min_text_chars": "native_text_chars",
        "min_extracted_text_chars": "extracted_text_chars",
        "min_outline_nodes": "outline_nodes",
        "min_tables": "tables",
        "min_figures": "figures",
        "min_formulas": "formulas",
        "min_references": "references",
        "min_authors": "authors",
        "min_ocr_pages": "ocr_page_count",
    }
    for expectation_key, metric_key in minimums.items():
        minimum = expected.get(expectation_key)
        if minimum is not None and metrics[metric_key] < minimum:
            failures.append(f"{metric_key}={metrics[metric_key]} < {minimum}")
    maximums = {
        "max_figures": "figures",
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


def _validate_structure(parsed: ParsedDocument, expected: dict[str, Any]) -> list[str]:
    """Content/anchor gates supplement counts; counts alone cannot prove correctness."""
    failures = []
    for name in expected.get("required_authors", []):
        if name not in parsed.authors:
            failures.append(f"missing author: {name}")
    if "authors_exact" in expected and parsed.authors != expected["authors_exact"]:
        failures.append("author list differs from source gold")
    if "affiliations_exact" in expected and parsed.affiliations != expected["affiliations_exact"]:
        failures.append("affiliation list differs from source gold")
    for fragment in expected.get("required_affiliations", []):
        if not any(fragment in item for item in parsed.affiliations):
            failures.append(f"missing affiliation: {fragment}")
    reference_text = "\n".join(ref.text for ref in parsed.references).casefold()
    for fragment in expected.get("reference_text_contains", []):
        if fragment.casefold() not in reference_text:
            failures.append(f"missing reference text: {fragment}")
    for fragment in expected.get("reference_text_excludes", []):
        if fragment.casefold() in reference_text:
            failures.append(f"non-reference text leaked: {fragment}")
    labels = {ref.label for ref in parsed.references}
    for label in expected.get("reference_labels_contains", []):
        if label not in labels:
            failures.append(f"missing reference label: {label}")
    figure_counts = Counter(str(figure.page_number) for figure in parsed.figures)
    for page, count in expected.get("figure_count_by_page", {}).items():
        if figure_counts[page] != count:
            failures.append(f"page {page} figures={figure_counts[page]} != {count}")
    matched_reference_ids = []
    for sample in expected.get("reference_samples", []):
        source_page = sample["page_number"]
        # A structured reference stores its printed label separately from its text.
        # Keep the label requirement, but do not demand a duplicate label in the body.
        label_match = re.match(
            r"^\s*\[([A-Za-z0-9+._:-]+)\]\s*(.+)",
            sample["contains"], flags=re.DOTALL,
        )
        # Unbracketed labels must be explicit, so a leading year is not mistaken for a label.
        label = sample.get("label", label_match.group(1) if label_match else None)
        fragment = _normalized_text(label_match.group(2) if label_match else sample["contains"])
        matches = [ref.reference_id for ref in parsed.references if (
            ref.page_number == source_page and (label is None or ref.label == label)
            and fragment in _normalized_text(ref.text)
        )]
        if not matches:
            failures.append(f"missing source reference sample on page {source_page}: "
                            f"{sample['contains']}")
        elif expected.get("reference_samples_distinct"):
            if len(matches) != 1:
                failures.append("source reference sample matches multiple entries")
            matched_reference_ids.extend(matches)
    if (expected.get("reference_samples_distinct")
            and len(set(matched_reference_ids)) != len(matched_reference_ids)):
        failures.append("distinct source reference samples were merged into one entry")
    for sample in expected.get("page_text_samples", []):
        page = next((p for p in parsed.pages if p.page_number == sample["page_number"]), None)
        text = _normalized_text(" ".join(block.text for block in page.blocks)) if page else ""
        if _normalized_text(sample["contains"]) not in text:
            failures.append(f"missing source text sample on page {sample['page_number']}")
    for page in parsed.pages:
        if len({block.block_id for block in page.blocks}) != len(page.blocks):
            failures.append(f"duplicate source block ID on page {page.page_number}")
        for block in page.blocks:
            if (len(block.bbox) != 4 or not all(math.isfinite(value) for value in block.bbox)
                    or block.bbox[0] > block.bbox[2] or block.bbox[1] > block.bbox[3]):
                failures.append(f"invalid block coordinates: {block.block_id}")
    # Every reference must resolve to actual source blocks on its anchor page.
    for ref in parsed.references:
        page = next((page for page in parsed.pages if page.page_number == ref.page_number), None)
        blocks = [block for block in page.blocks if block.block_id in ref.block_ids] if page else []
        if not blocks or not all(
            ref.bbox[0] <= b.bbox[0]
            and ref.bbox[1] <= b.bbox[1]
            and ref.bbox[2] >= b.bbox[2]
            and ref.bbox[3] >= b.bbox[3]
            for b in blocks
        ):
            failures.append(f"invalid reference anchor: {ref.reference_id}")
    return failures


def _normalized_text(text: str) -> str:
    """Normalize typography for source probes, not numerical values or author spelling."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _count_outline(nodes: list[Any]) -> int:
    return sum(1 + _count_outline(node.children) for node in nodes)


def load_expectations(manifest_path: Path | None) -> dict[str, dict[str, Any]]:
    if manifest_path is None:
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = [*manifest.get("downloads", []), *manifest.get("local_fixtures", [])]
    return {item["filename"]: item.get("expect", {}) for item in items}


def evaluate_pdf_safely(
    path: Path,
    expectation: dict[str, Any] | None = None,
    *,
    batch_pages: int | None = None,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        return evaluate_pdf(path, expectation, batch_pages=batch_pages, artifacts_dir=artifacts_dir)
    except Exception as exc:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
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
    parser.add_argument("--batch-pages", type=int, default=25)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    expectations = load_expectations(args.manifest)
    pdfs = sorted(args.corpus_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDF files found in {args.corpus_dir}")
    results = [
        evaluate_pdf_safely(
            path,
            expectations.get(path.name),
            batch_pages=max(1, args.batch_pages),
        )
        for path in pdfs
    ]
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
