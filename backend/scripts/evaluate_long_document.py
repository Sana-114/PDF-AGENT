"""Evaluate checkpoint interruption and resume behavior on a long PDF."""

import argparse
import hashlib
import json
import math
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - Windows fallback for local execution
    resource = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # noqa: E402

from app.parsers.checkpoint import read_manifest  # noqa: E402
from app.parsers.registry import get_parser  # noqa: E402


class PlannedInterruption(RuntimeError):
    """Raised only after a complete page batch has been committed."""


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _count_outline(nodes: list[Any]) -> int:
    return sum(1 + _count_outline(node.children) for node in nodes)


def evaluate_long_document(
    path: Path,
    *,
    batch_pages: int,
    interrupt_after_pages: int,
    expected_pages: int | None = None,
    max_elapsed_seconds: float = 900,
    max_python_peak_memory_mb: float = 512,
) -> dict[str, Any]:
    batch_pages = max(1, batch_pages)
    with fitz.open(path) as source:
        page_count = len(source)
        native_text_chars = sum(len(page.get_text("text")) for page in source)
    if page_count < 2:
        raise ValueError("Long-document resume evaluation requires at least two pages")
    interruption_target = min(
        math.ceil(max(1, interrupt_after_pages) / batch_pages) * batch_pages,
        page_count - 1,
    )
    source_fingerprint = _sha256(path)
    first_progress: list[tuple[int, int]] = []
    resumed_progress: list[tuple[int, int]] = []

    tracemalloc.start()
    total_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="paperpilot-long-resume-") as directory:
        checkpoint_dir = Path(directory)
        first_started = time.perf_counter()

        def interrupt_after_batch(completed: int, total: int) -> None:
            first_progress.append((completed, total))
            if completed >= interruption_target:
                raise PlannedInterruption(f"planned interruption after page {completed}")

        try:
            get_parser(str(path)).parse(
                str(path),
                checkpoint_dir=checkpoint_dir,
                batch_size=batch_pages,
                source_fingerprint=source_fingerprint,
                progress_callback=interrupt_after_batch,
            )
        except PlannedInterruption:
            pass
        else:
            raise AssertionError("Planned checkpoint interruption did not occur")
        first_elapsed = time.perf_counter() - first_started
        interrupted_manifest = read_manifest(checkpoint_dir) or {}
        interrupted_pages = int(interrupted_manifest.get("completed_pages", 0))

        resume_started = time.perf_counter()
        parsed = get_parser(str(path)).parse(
            str(path),
            checkpoint_dir=checkpoint_dir,
            batch_size=batch_pages,
            source_fingerprint=source_fingerprint,
            progress_callback=lambda complete, total: resumed_progress.append(
                (complete, total)
            ),
        )
        resume_elapsed = time.perf_counter() - resume_started
        final_manifest = read_manifest(checkpoint_dir) or {}
        checkpoint_batches = len(final_manifest.get("batches", []))

    total_elapsed = time.perf_counter() - total_started
    _, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    process_peak_mb = None
    if resource is not None:
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        process_peak_mb = peak_rss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    expected_page_count = expected_pages or page_count
    expected_first_resume_page = min(interrupted_pages + batch_pages, page_count)
    first_resumed_page = resumed_progress[0][0] if resumed_progress else page_count
    checks = {
        "exact_page_count": len(parsed.pages) == expected_page_count,
        "interruption_checkpoint_committed": interrupted_pages == interruption_target,
        "resume_skipped_completed_batches": first_resumed_page == expected_first_resume_page,
        "final_checkpoint_complete": final_manifest.get("completed_pages") == page_count,
        "all_batches_present": checkpoint_batches == math.ceil(page_count / batch_pages),
        "text_extracted": len(parsed.full_text) >= native_text_chars * 0.95,
        "elapsed_within_limit": total_elapsed <= max_elapsed_seconds,
        "python_memory_within_limit": (
            python_peak / 1024 / 1024 <= max_python_peak_memory_mb
        ),
    }
    return {
        "schema_version": "1.0",
        "status": "passed" if all(checks.values()) else "failed",
        "filename": path.name,
        "checks": checks,
        "metrics": {
            "page_count": len(parsed.pages),
            "native_text_chars": native_text_chars,
            "extracted_text_chars": len(parsed.full_text),
            "title": parsed.title,
            "authors": len(parsed.authors),
            "outline_nodes": _count_outline(parsed.outline),
            "tables": len(parsed.tables),
            "figures": len(parsed.figures),
            "formulas": len(parsed.formulas),
            "references": len(parsed.references),
            "batch_size_pages": batch_pages,
            "interrupted_after_pages": interrupted_pages,
            "first_resumed_page": first_resumed_page,
            "checkpoint_batches": checkpoint_batches,
            "first_pass_seconds": round(first_elapsed, 3),
            "resume_seconds": round(resume_elapsed, 3),
            "total_seconds": round(total_elapsed, 3),
            "python_peak_memory_mb": round(python_peak / 1024 / 1024, 2),
            "process_peak_rss_mb": (
                round(process_peak_mb, 2) if process_peak_mb is not None else None
            ),
            "warnings": parsed.warnings,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--batch-pages", type=int, default=25)
    parser.add_argument("--interrupt-after-pages", type=int, default=50)
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--max-elapsed-seconds", type=float, default=900)
    parser.add_argument("--max-python-peak-memory-mb", type=float, default=512)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = evaluate_long_document(
        args.pdf,
        batch_pages=args.batch_pages,
        interrupt_after_pages=args.interrupt_after_pages,
        expected_pages=args.expected_pages,
        max_elapsed_seconds=args.max_elapsed_seconds,
        max_python_peak_memory_mb=args.max_python_peak_memory_mb,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.strict and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
