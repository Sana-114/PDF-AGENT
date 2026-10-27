"""Isolated, zero-LLM parser checks with immutable inputs and per-document timeout/reporting."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_selection(corpus: Path, manifest_path: Path, filenames: list[str] | None = None) -> list:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("downloads", [])
    if not items or len({item["filename"] for item in items}) != len(items):
        raise ValueError("Manifest must contain unique, explicit document checks")
    by_name = {item["filename"]: item for item in items}
    names = filenames if filenames is not None else list(by_name)
    if not names or len(names) != len(set(names)) or set(names) - set(by_name):
        raise ValueError("Empty, duplicate, or unknown selection")
    selected = []
    resolved_corpus = corpus.resolve()
    for name in names:
        item = by_name[name]
        if Path(name).name != name or not item.get("expect"):
            raise ValueError(f"Unsafe filename or missing acceptance checks: {name}")
        path = (corpus / name).resolve()
        if path.parent != resolved_corpus:
            raise ValueError(f"PDF resolves outside the selected corpus: {name}")
        if file_hash(path) != item.get("sha256"):
            raise ValueError(f"Source fingerprint mismatch: {name}")
        selected.append(item)
    return selected


def summarize(documents: list[dict]) -> dict:
    return {
        "selected": len(documents),
        "completed": sum(item["status"] != "not_run" for item in documents),
        "scoped_checks_passed": sum(item["status"] == "passed" for item in documents),
        "scoped_checks_failed": sum(item["status"] == "failed" for item in documents),
        "errors": sum(item["status"] == "error" for item in documents),
        "timeouts": sum(item["status"] == "timeout" for item in documents),
        "not_run": sum(item["status"] == "not_run" for item in documents),
        "parsed_complete": sum(item.get("parsed_page_count", -1) == item.get("page_count", -2)
                               for item in documents),
    }


def write_report(path: Path, report: dict) -> None:
    report["summary"] = summarize(report["documents"])
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_batch(corpus: Path, manifest: Path, report_dir: Path, *, batch_size: int = 8,
              timeout_seconds: float = 300, filenames: list[str] | None = None) -> dict:
    if batch_size < 1 or timeout_seconds <= 0:
        raise ValueError("Batch size and timeout must be positive")
    selected = load_selection(corpus, manifest, filenames)
    report_dir.mkdir(parents=True, exist_ok=False)
    script_paths = [Path(__file__), BACKEND_ROOT / "scripts/evaluate_pdf_corpus.py",
                    *sorted((BACKEND_ROOT / "app/parsers").glob("*.py"))]
    report = {
        "schema_version": "1.0", "timestamp": datetime.now(UTC).isoformat(),
        "evaluation_kind": "native_official_pdf_scoped_parser_checks",
        "acceptance_scope": "metadata, complete page sequence, text volume, minimum structures, "
        "native-source-derived reference probes, and source coordinates; NOT full six-module QA",
        "manifest_sha256": file_hash(manifest),
        "code_sha256": {str(path.relative_to(BACKEND_ROOT)): file_hash(path)
                        for path in script_paths},
        "model_calls": 0, "embedding_calls": 0, "automatic_retries": 0,
        "batch_size_documents": batch_size, "timeout_seconds_per_document": timeout_seconds,
        "python_memory_note": "tracemalloc measures Python allocations, not total RAM or VRAM",
        "documents": [{"filename": item["filename"], "status": "not_run"} for item in selected],
    }
    report_path = report_dir / "report.json"
    write_report(report_path, report)
    child_env = os.environ.copy()
    child_env.update(LLM_PROVIDER="mock", LLM_API_KEY="", VECTOR_SEARCH_ENABLED="false",
                     EMBEDDING_PROVIDER="hash", RERANKER_PROVIDER="none", PYTHONIOENCODING="utf-8")
    started = time.perf_counter()
    for index, item in enumerate(selected):
        job = report_dir / f"document-{index + 1:02d}"
        job.mkdir()
        command = [sys.executable, str(Path(__file__).resolve()), "--corpus", str(corpus.resolve()),
                   "--manifest", str(manifest.resolve()), "--report-dir", str(job.resolve()),
                   "--worker", item["filename"]]
        print(f"[{index + 1}/{len(selected)}] {item['filename']} starting", flush=True)
        try:
            completed = subprocess.run(command, env=child_env, capture_output=True,
                                       timeout=timeout_seconds, check=False)
            (job / "stdout.log").write_bytes(completed.stdout)
            (job / "stderr.log").write_bytes(completed.stderr)
            result_path = job / "result.json"
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result["worker_exit_code"] = completed.returncode
                if completed.returncode not in (0, 1):
                    result["status"] = "error"
                    result.setdefault("failures", []).append("Unexpected worker exit code")
            else:
                result = {"filename": item["filename"], "status": "error",
                          "failures": ["Worker did not produce a result"],
                          "worker_exit_code": completed.returncode}
        except subprocess.TimeoutExpired as exc:
            (job / "stdout.log").write_bytes(exc.stdout or b"")
            (job / "stderr.log").write_bytes(exc.stderr or b"")
            result = {"filename": item["filename"], "status": "timeout",
                      "failures": [f"Exceeded {timeout_seconds}s; no automatic retry"]}
        result["source_sha256"] = item["sha256"]
        result["batch_number"] = index // batch_size + 1
        result["artifacts_directory"] = job.name
        report["documents"][index] = result
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        write_report(report_path, report)
        print(f"[{index + 1}/{len(selected)}] {item['filename']} {result['status']} "
              f"({len(result.get('failures', []))} failed checks)", flush=True)
        if (index + 1) % batch_size == 0 or index + 1 == len(selected):
            batch_number = index // batch_size + 1
            batch_docs = [doc for doc in report["documents"]
                          if doc.get("batch_number") == batch_number]
            write_report(report_dir / f"batch-{batch_number}.json", {
                "schema_version": "1.0", "model_calls": 0, "documents": batch_docs,
                "acceptance_scope": report["acceptance_scope"],
            })
    return report


def worker(corpus: Path, manifest: Path, report_dir: Path, filename: str) -> dict:
    from scripts.evaluate_pdf_corpus import evaluate_pdf_safely

    item = load_selection(corpus, manifest, [filename])[0]
    result = evaluate_pdf_safely(corpus / filename, item["expect"], batch_pages=25,
                               artifacts_dir=report_dir / "artifacts")
    # Exceptions differ from quality-check failures and must not be counted as successful parsing.
    if "parsed_page_count" not in result:
        result["status"] = "error"
    (report_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--filenames", nargs="+")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        result = worker(args.corpus, args.manifest, args.report_dir, args.worker)
        raise SystemExit(0 if result["status"] == "passed" else 1)
    result = run_batch(args.corpus, args.manifest, args.report_dir, batch_size=args.batch_size,
                       timeout_seconds=args.timeout_seconds, filenames=args.filenames)
    print(json.dumps(result["summary"]))
    raise SystemExit(0 if result["summary"]["scoped_checks_passed"]
                     == result["summary"]["selected"] else 1)


if __name__ == "__main__":
    main()
