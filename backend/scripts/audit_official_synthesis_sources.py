"""Read-only source audit for saved official synthesis outputs; no model calls.

Checks literal AST/PDF locations independently of generated citation numbering.
Optional full-page Poppler renders are for subsequent manual scientific review.
"""

import argparse
import json
import subprocess
from pathlib import Path

import fitz
from official_functional_inputs import digest, isolated_output, write_json
from verify_official_local_features import compact


def run(args):
    root = isolated_output(args.output_dir)
    records, pages = [], {}
    for kind, filename in (("review", "review.json"), ("writing", "writing-outline.json")):
        folder = args.batch / kind
        result = json.loads((folder / filename).read_bytes())
        report = json.loads((folder / "report.json").read_bytes())
        sources = {r["document_id"]: r for r in report["sources"]}
        used = {key for c in [*result["claims"], *result.get("deferred_claims", [])]
                for key in c["evidence_ids"]}
        for anchor in result["evidence"]:
            if anchor["evidence_id"] not in used:
                continue
            doc_id, number = anchor["document_id"], anchor["page_number"]
            row = sources[doc_id]
            pdf = folder / "uploads" / (doc_id + ".pdf")
            ast_file = folder / "parsed" / (doc_id + ".json")
            ast = json.loads(ast_file.read_bytes())
            page = next(p for p in ast["pages"] if p["page_number"] == number)
            blocks = {b["block_id"]: b for b in page["blocks"]}
            ids = anchor["block_ids"]
            raw = " ".join(blocks[key]["text"] for key in ids if key in blocks)
            with fitz.open(pdf) as native:
                native_text = native[number - 1].get_text()
            checks = {
                "source_sha": digest(pdf) == row["source_sha256"],
                "ast_sha": digest(ast_file) == row["ast_sha256"],
                "page_and_blocks": bool(ids) and all(key in blocks for key in ids),
                "literal_ast": compact(anchor["quote"]) in compact(raw),
                "literal_native_page": compact(anchor["quote"]) in compact(native_text),
            }
            records.append(
                {
                    "mode": kind,
                    "evidence_id": anchor["evidence_id"],
                    "filename": row["filename"],
                    "physical_page": number,
                    "checks": checks,
                }
            )
            pages[(row["filename"], number)] = pdf
    renders = []
    if args.poppler:
        for (name, number), pdf in pages.items():
            prefix = root / (Path(name).stem + f"-p{number}")
            result = subprocess.run(
                [
                    str(args.poppler),
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-r",
                    "95",
                    "-singlefile",
                    "-png",
                    str(pdf),
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            renders.append(
                {
                    "filename": name,
                    "physical_page": number,
                    "image": str(prefix) + ".png",
                    "exit_code": result.returncode,
                    "stderr": result.stderr[:1000],
                }
            )
    report = {
        "status": "literal_checks_passed"
        if all(all(r["checks"].values()) for r in records)
        else "needs_inspection",
        "anchors": records,
        "renders": renders,
        "cloud_calls": 0,
        "scientific_review": "pending_manual_full_page_review",
    }
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "anchors": len(records),
                "pages": len(pages),
                "nonliteral": [r for r in records if not all(r["checks"].values())],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--poppler", type=Path)
    run(parser.parse_args())
