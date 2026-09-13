"""Run the production duplicate and revision-diff logic on two local PDFs."""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.parsers.registry import get_parser  # noqa: E402
from app.services.document_duplicates import assess_duplicate, recommend_version  # noqa: E402
from app.services.document_version_diff import compare_document_versions  # noqa: E402
from app.services.fingerprints import (  # noqa: E402
    bottom_k_signature,
    extract_document_arxiv_identity,
)


def _parse(path: Path, batch_pages: int) -> Any:
    parser = get_parser(str(path))
    with tempfile.TemporaryDirectory(prefix="paperpilot-version-pair-") as directory:
        return parser.parse(
            str(path),
            checkpoint_dir=Path(directory),
            batch_size=max(1, batch_pages),
        )


def evaluate_pair(existing_path: Path, current_path: Path, *, batch_pages: int) -> dict:
    """Model the contest flow: upload the existing PDF, then the current PDF."""

    existing_parsed = _parse(existing_path, batch_pages)
    current_parsed = _parse(current_path, batch_pages)
    existing_arxiv = extract_document_arxiv_identity(
        existing_path.name, existing_parsed.full_text
    )
    current_arxiv = extract_document_arxiv_identity(current_path.name, current_parsed.full_text)
    existing_signature = bottom_k_signature(existing_parsed.full_text)
    current_signature = bottom_k_signature(current_parsed.full_text)
    assessment = assess_duplicate(
        current_arxiv_id=current_arxiv[0],
        current_title=current_parsed.title,
        current_signature=current_signature,
        existing_arxiv_id=existing_arxiv[0],
        existing_title=existing_parsed.title,
        existing_signature=existing_signature,
    )
    recommendation = recommend_version(current_arxiv[1], existing_arxiv[1])
    existing = SimpleNamespace(
        id="existing-document",
        original_filename=existing_path.name,
        arxiv_version=existing_arxiv[1],
        page_count=len(existing_parsed.pages),
    )
    current = SimpleNamespace(
        id="current-document",
        original_filename=current_path.name,
        arxiv_version=current_arxiv[1],
        page_count=len(current_parsed.pages),
    )
    difference = compare_document_versions(
        current=current,
        existing=existing,
        current_parsed=current_parsed.to_dict(),
        existing_parsed=existing_parsed.to_dict(),
    )
    checks = {
        "same_arxiv_id": current_arxiv[0] == existing_arxiv[0] == "1706.03762",
        "existing_is_v7": existing_arxiv[1] == 7,
        "current_is_v1": current_arxiv[1] == 1,
        "duplicate_warning_triggered": assessment.qualifies,
        "newer_existing_version_recommended": "已有更新的 arXiv v7" in recommendation,
        "evidence_linked_difference_available": bool(
            difference["possibly_added_passages"]
            or difference["possibly_removed_passages"]
            or difference["added_headings"]
            or difference["removed_headings"]
        ),
    }
    documents = [
        {
            "role": role,
            "filename": path.name,
            "title": parsed.title,
            "authors": parsed.authors,
            "page_count": len(parsed.pages),
            "arxiv_id": identity[0],
            "arxiv_version": identity[1],
        }
        for role, path, parsed, identity in (
            ("existing", existing_path, existing_parsed, existing_arxiv),
            ("current", current_path, current_parsed, current_arxiv),
        )
    ]
    return {
        "schema_version": "1.0",
        "status": "passed" if all(checks.values()) else "failed",
        "scenario": "upload-v7-then-v1",
        "checks": checks,
        "documents": documents,
        "duplicate_assessment": assessment.to_dict(),
        "recommendation": recommendation,
        "version_difference": difference,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("existing_pdf", type=Path, help="PDF already stored in the library")
    parser.add_argument("current_pdf", type=Path, help="PDF uploaded after the existing PDF")
    parser.add_argument("--batch-pages", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = evaluate_pair(
        args.existing_pdf,
        args.current_pdf,
        batch_pages=max(1, args.batch_pages),
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
