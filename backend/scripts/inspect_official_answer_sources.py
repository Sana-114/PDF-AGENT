"""Render unchanged public source pages cited by a preserved official answer run.

Offline source inspection material only: no LLM, no regrading, no index access.
Native page text and PNGs are not a proof that every model statement is correct.
"""

import argparse
import hashlib
import json
from pathlib import Path

import fitz


def inspect(report_path: Path, corpus: Path, supplements: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise ValueError("Source inspection may not overwrite previous artifacts")
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    records = {item["id"]: item for item in report["documents"]}
    requests = {}
    for case in report["cases"]:
        response = case.get("response") or {}
        cited = {key for claim in response.get("claims", []) for key in claim["evidence_ids"]}
        for anchor in response.get("evidence", []):
            if anchor["evidence_id"] not in cited:
                continue
            if anchor["document_id"] not in records:
                raise ValueError("A cited document is outside the preserved public run")
            key = (anchor["document_id"], anchor["page_number"])
            requests.setdefault(key, []).append({"case_id": case["case_id"], "anchor": anchor})
    verified_paths = {}
    for doc_id, _ in requests:
        item = records[doc_id]
        name = item["filename"]
        if Path(name).name != name or item["origin"] not in {"official", "public_supplement"}:
            raise ValueError("Source is not a declared public paper filename")
        path = (supplements if item["origin"] == "public_supplement" else corpus) / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Public source PDF identity changed")
        verified_paths[doc_id] = path
    output_dir.mkdir(parents=True)
    pages = []
    for (doc_id, page_number), anchors in requests.items():
        item = records[doc_id]
        with fitz.open(verified_paths[doc_id]) as native:
            if len(native) != item["pages"] or not 1 <= page_number <= len(native):
                raise ValueError("Physical page count or anchor page differs")
            page = native[page_number - 1]
            image_path = output_dir / f"{Path(item['filename']).stem}-page-{page_number}.png"
            page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(str(image_path))
            pages.append({
                "filename": item["filename"], "pdf_sha256": item["sha256"],
                "page_number": page_number, "page_rect": list(page.rect),
                "native_text": page.get_text("text", sort=True),
                "image_path": str(image_path.resolve()),
                "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "cited_anchors": anchors,
            })
    result = {
        "evaluation_kind": "offline_native_source_page_inspection_material",
        "generation_calls": 0, "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "reviewer_verdict": "not_assigned_by_this_tool", "pages": pages,
    }
    (output_dir / "sources.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--supplements", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.report, args.corpus, args.supplements, args.output_dir)
    print(json.dumps({"generation_calls": 0, "rendered_pages": len(result["pages"])}))
