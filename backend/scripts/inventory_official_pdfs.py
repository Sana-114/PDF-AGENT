"""Read-only inventory of organizer PDFs; does not parse through an LLM or ingest."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import fitz


def inventory(corpus: Path) -> dict:
    documents = []
    for path in sorted(corpus.glob("*.pdf")):
        item = {"filename": path.name, "size_bytes": path.stat().st_size}
        with path.open("rb") as stream:
            item["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        try:
            with fitz.open(path) as document:
                char_counts = [len(page.get_text("text").strip()) for page in document]
                item.update(
                    status="readable",
                    pages=len(document),
                    native_text_characters=sum(char_counts),
                    pages_without_native_text=sum(count == 0 for count in char_counts),
                )
        except Exception as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        documents.append(item)
    return {
        "schema_version": "1.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": "file_inventory_not_parser_acceptance",
        "status": "passed" if documents and all(
            item["status"] == "readable" for item in documents
        ) else "failed",
        "summary": {
            "pdf_files": len(documents),
            "pages": sum(item.get("pages", 0) for item in documents),
            "bytes": sum(item["size_bytes"] for item in documents),
        },
        "documents": documents,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = inventory(args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["summary"]}))
    if report["status"] != "passed":
        raise SystemExit(1)
