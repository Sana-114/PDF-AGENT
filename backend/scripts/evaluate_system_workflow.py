"""Exercise the deployed upload, parse, reader, and version-warning workflow."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def _wait_http(client: httpx.Client, path: str, timeout_seconds: float) -> httpx.Response:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if response.status_code < 500:
                return response
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        time.sleep(1)
    raise TimeoutError(f"Service did not become ready: {path} ({last_error})")


def _wait_document(
    client: httpx.Client,
    document_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"
    while time.monotonic() < deadline:
        response = client.get(f"/documents/{document_id}")
        response.raise_for_status()
        document = response.json()
        last_status = str(document.get("status", "unknown"))
        if last_status == "ready":
            return document
        if last_status == "failed":
            raise RuntimeError(document.get("error_message") or "Document parsing failed")
        time.sleep(1.5)
    raise TimeoutError(f"Document {document_id} remained {last_status}")


def _upload_variant(
    client: httpx.Client,
    source: Path,
    *,
    filename: str,
    marker: str,
) -> dict[str, Any]:
    payload = source.read_bytes() + f"\n% PaperPilot E2E {marker}\n".encode()
    response = client.post(
        "/documents",
        files={"file": (filename, payload, "application/pdf")},
    )
    response.raise_for_status()
    return response.json()


def evaluate_system_workflow(
    *,
    api_base_url: str,
    frontend_url: str,
    transformer_v7: Path,
    transformer_v1: Path,
    timeout_seconds: float = 300,
    cleanup: bool = False,
) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%H%M%S") + f"-{time.time_ns() % 100_000:05d}"
    synthetic_arxiv_id = f"9900.{time.time_ns() % 100_000:05d}"
    checks: dict[str, bool] = {}
    metrics: dict[str, Any] = {"run_id": run_id, "arxiv_id": synthetic_arxiv_id}
    created_ids: list[str] = []
    started = time.perf_counter()

    with httpx.Client(base_url=api_base_url.rstrip("/"), timeout=60) as client:
        try:
            health = _wait_http(client, "/health", min(timeout_seconds, 120))
            checks["backend_health"] = health.status_code == 200 and health.json().get(
                "status"
            ) == "ok"

            with httpx.Client(timeout=30, follow_redirects=True) as frontend_client:
                frontend = _wait_http(
                    frontend_client,
                    frontend_url.rstrip("/"),
                    min(timeout_seconds, 120),
                )
            checks["frontend_http"] = frontend.status_code == 200
            checks["frontend_shell"] = "PaperPilot" in frontend.text

            latest_upload = _upload_variant(
                client,
                transformer_v7,
                filename=f"paperpilot-e2e-{synthetic_arxiv_id}v7.pdf",
                marker=f"{run_id}-v7",
            )
            latest_id = str(latest_upload["document"]["id"])
            created_ids.append(latest_id)
            checks["latest_not_exact_duplicate"] = not latest_upload["exact_duplicate"]
            latest = _wait_document(client, latest_id, timeout_seconds)
            checks["latest_ready"] = latest["status"] == "ready"
            checks["latest_version"] = (
                latest.get("arxiv_id") == synthetic_arxiv_id
                and latest.get("arxiv_version") == 7
            )

            progress_response = client.get(f"/documents/{latest_id}/progress")
            progress_response.raise_for_status()
            progress = progress_response.json()
            checks["progress_complete"] = (
                progress.get("percentage") == 100.0
                and progress.get("completed_pages") == latest.get("page_count")
            )

            content_response = client.get(f"/documents/{latest_id}/content")
            content_response.raise_for_status()
            content = content_response.json()
            metrics["latest_structure"] = {
                "pages": len(content.get("pages", [])),
                "tables": len(content.get("tables", [])),
                "figures": len(content.get("figures", [])),
                "formulas": len(content.get("formulas", [])),
                "references": len(content.get("references", [])),
            }
            checks["structured_content"] = (
                metrics["latest_structure"]["pages"] >= 12
                and metrics["latest_structure"]["tables"] >= 4
                and metrics["latest_structure"]["formulas"] >= 8
                and metrics["latest_structure"]["references"] >= 30
            )

            outline_response = client.get(f"/documents/{latest_id}/outline")
            outline_response.raise_for_status()
            checks["reader_outline"] = bool(outline_response.json().get("items"))

            references_response = client.get(f"/documents/{latest_id}/references")
            references_response.raise_for_status()
            references = references_response.json()
            metrics["reader_references"] = len(references.get("items", []))
            metrics["reader_mentions"] = len(references.get("mentions", []))
            checks["reader_reference_links"] = (
                metrics["reader_references"] >= 30 and metrics["reader_mentions"] > 0
            )

            range_response = client.get(
                f"/documents/{latest_id}/file",
                headers={"Range": "bytes=0-63"},
            )
            checks["reader_pdf_range"] = (
                range_response.status_code == 206
                and range_response.content.startswith(b"%PDF-")
                and len(range_response.content) == 64
            )

            older_upload = _upload_variant(
                client,
                transformer_v1,
                filename=f"paperpilot-e2e-{synthetic_arxiv_id}v1.pdf",
                marker=f"{run_id}-v1",
            )
            older_id = str(older_upload["document"]["id"])
            created_ids.append(older_id)
            checks["older_not_exact_duplicate"] = not older_upload["exact_duplicate"]
            older = _wait_document(client, older_id, timeout_seconds)
            checks["semantic_duplicate_warning"] = (
                older.get("duplicate_of_id") == latest_id
                and older.get("duplicate_score") is not None
            )
            checks["newer_version_recommended"] = (
                "v7" in str(older.get("duplicate_recommendation"))
                and "建议保留" in str(older.get("duplicate_recommendation"))
            )

            diff_response = client.get(f"/documents/{older_id}/duplicates/diff")
            diff_response.raise_for_status()
            difference = diff_response.json()
            metrics["version_content_overlap"] = difference.get("content_overlap")
            checks["version_diff"] = (
                difference.get("current_document_id") == older_id
                and difference.get("existing_document_id") == latest_id
                and float(difference.get("content_overlap", 0)) > 0.5
            )
        finally:
            if cleanup:
                for document_id in reversed(created_ids):
                    try:
                        client.delete(f"/documents/{document_id}")
                    except httpx.HTTPError:
                        pass

    metrics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return {
        "schema_version": "1.0",
        "status": "passed" if checks and all(checks.values()) else "failed",
        "checks": checks,
        "metrics": metrics,
        "created_document_ids": created_ids,
        "cleanup_requested": cleanup,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transformer_v7", type=Path)
    parser.add_argument("transformer_v1", type=Path)
    parser.add_argument("--api-base-url", default="http://localhost:8000/api/v1")
    parser.add_argument("--frontend-url", default="http://frontend:3000")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = evaluate_system_workflow(
        api_base_url=args.api_base_url,
        frontend_url=args.frontend_url,
        transformer_v7=args.transformer_v7,
        transformer_v1=args.transformer_v1,
        timeout_seconds=args.timeout_seconds,
        cleanup=args.cleanup,
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
