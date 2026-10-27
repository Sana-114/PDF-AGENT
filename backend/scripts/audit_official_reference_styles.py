"""Audit persisted official reference styles without pretending all are linkable.

Reads pinned source PDFs/AST only, renders complete example pages for review.
Author-year-like examples are heuristic probes, not a citation-resolution oracle.
"""

import argparse
import json
import os
import re
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_output_features import configure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    names = [r["filename"] for r in json.loads((args.fixture / "fixture.json").read_bytes())]
    inputs = saved_inputs(args.fixture, names)
    root = isolated_output(args.output_dir)
    configure(root)
    os.environ.update(LLM_PROVIDER="mock", LLM_API_KEY="", VECTOR_SEARCH_ENABLED="false")
    import pymupdf

    from app.services.document_content import reference_links

    author_year = re.compile(r"\b[A-Z][A-Za-z'-]+(?:\s+et\s+al\.)[,\s]+\(?\d{4}[a-z]?\)?")
    records, pages = [], []
    for item in inputs:
        parsed = item["parsed"]
        refs, mentions = reference_links(parsed)
        year_refs = [r for r in refs if str(r["label"]).startswith("author-year-")]
        reference_ids = {b for r in refs for b in r.get("block_ids", [])}
        examples = []
        for page in parsed["pages"]:
            for block in page["blocks"]:
                if block.get("block_id") in reference_ids:
                    continue
                text = " ".join(str(block.get("text") or "").split())
                found = author_year.search(text)
                if found and len(examples) < 2:
                    examples.append(
                        {
                            "page": page["page_number"],
                            "block_id": block["block_id"],
                            "candidate": found.group(),
                            "bbox": block.get("bbox"),
                        }
                    )
        linked_year = [m for m in mentions if m["label"].startswith("author-year-")]
        row = {
            "filename": item["row"]["filename"],
            "source_sha256": item["row"]["source_sha256"],
            "ast_sha256": item["ast_sha256"],
            "reference_count": len(refs),
            "author_year_references": len(year_refs),
            "bracket_mentions": len(mentions),
            "author_year_mentions": len(linked_year),
            "heuristic_body_examples": examples,
            "author_year_gap_observed": bool(year_refs and examples and not linked_year),
        }
        if row["author_year_gap_observed"] and len(pages) < 2:
            number = examples[0]["page"]
            with pymupdf.open(item["source"]) as pdf:
                image = root / (Path(row["filename"]).stem + f"-p{number}.png")
                pdf[number - 1].get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4)).save(image)
            pages.append({"filename": row["filename"], "page": number, "image": image.name})
        records.append(row)
    report = {
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "status": "audit_completed_with_known_gaps"
        if any(r["author_year_gap_observed"] for r in records)
        else "audit_completed_no_heuristic_gap_found",
        "sources": records,
        "full_pages_rendered": pages,
        "cloud_calls": 0,
        "BGE_calls": 0,
        "new_parses": 0,
        "browser_acceptance": False,
        "heuristic_examples_not_ground_truth": True,
        "production_library_modified": False,
    }
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "files": len(records),
                "author_year_gap_files": [
                    r["filename"] for r in records if r["author_year_gap_observed"]
                ],
                "rendered_pages": pages,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
