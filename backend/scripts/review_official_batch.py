"""Recheck saved parser ASTs; no reparse, generation, or changes to original results."""

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.parsers.base import Block, FigureNode, Page, ParsedDocument, ReferenceNode  # noqa: E402
from scripts.evaluate_official_batch import file_hash, summarize  # noqa: E402
from scripts.evaluate_pdf_corpus import _validate, _validate_structure  # noqa: E402


def restore_ast(payload: dict) -> ParsedDocument:
    pages = [Page(**{**page, "blocks": [Block(**block) for block in page["blocks"]]})
             for page in payload["pages"]]
    return ParsedDocument(
        schema_version=payload["schema_version"], title=payload["title"],
        authors=payload["authors"], pages=pages,
        full_text="\n".join(block.text for page in pages for block in page.blocks),
        affiliations=payload.get("affiliations", []),
        references=[ReferenceNode(**item) for item in payload["references"]],
        figures=[FigureNode(**item) for item in payload["figures"]],
    )


def review(report_path: Path, manifest_path: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("Do not overwrite any previous report")
    if output.resolve() in {report_path.resolve(), manifest_path.resolve()}:
        raise ValueError("Output cannot replace an input")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if file_hash(manifest_path) != report["manifest_sha256"]:
        raise ValueError("Manifest changed since the parser run")
    expectations = {item["filename"]: item for item in manifest["downloads"]}
    if report["summary"]["not_run"]:
        raise ValueError("Cannot review an incomplete batch as a complete corpus")
    documents = []
    artifacts = {}
    for original in report["documents"]:
        item = dict(original)
        if item["source_sha256"] != expectations[item["filename"]]["sha256"]:
            raise ValueError("Document hash differs from the original manifest")
        item["original_status"] = original["status"]
        item["original_failures"] = original.get("failures", [])
        if original["status"] in {"timeout", "error"}:
            documents.append(item)
            continue
        job_name = item["artifacts_directory"]
        if Path(job_name).name != job_name:
            raise ValueError("Unsafe artifact directory")
        ast_path = report_path.parent / job_name / "artifacts/parsed.json"
        parsed = restore_ast(json.loads(ast_path.read_text(encoding="utf-8")))
        expect = expectations[item["filename"]]["expect"]
        failures = [*_validate(item, expect), *_validate_structure(parsed, expect)]
        if [page.page_number for page in parsed.pages] != list(range(1, item["page_count"] + 1)):
            failures.append("parsed page sequence does not cover every source page exactly once")
        item["status"] = "failed" if failures else "passed"
        item["failures"] = failures
        artifacts[item["filename"]] = file_hash(ast_path)
        documents.append(item)
    result = {
        "schema_version": "1.0", "evaluation_kind": "saved_ast_scoped_recheck_not_new_parse",
        "source_report_sha256": file_hash(report_path),
        "manifest_sha256": file_hash(manifest_path),
        "review_script_sha256": file_hash(Path(__file__)),
        "validator_sha256": file_hash(BACKEND_ROOT / "scripts/evaluate_pdf_corpus.py"),
        "ast_sha256": artifacts, "model_calls": 0, "parser_calls": 0,
        "original_summary": report["summary"], "summary": summarize(documents),
        "acceptance_scope": report["acceptance_scope"], "documents": documents,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting even if another process creates the path meanwhile.
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = review(args.report, args.manifest, args.output)
    print(json.dumps(report["summary"]))
    raise SystemExit(0 if report["summary"]["scoped_checks_passed"]
                     == report["summary"]["selected"] else 1)


if __name__ == "__main__":
    main()
