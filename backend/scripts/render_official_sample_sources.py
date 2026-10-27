"""Render complete independent gold pages before judging the 39 generated samples.

This is offline inspection material, not an automatic scientific verdict. The
organizer PDFs and previous results are read-only; each output directory is new.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_official_all_document_qa import digest, normalized  # noqa: E402


def render(gold_path, corpus, poppler, output):
    if output.exists():
        raise ValueError("Do not overwrite previous source inspection material")
    cases = json.loads(gold_path.read_bytes())
    if len(cases) != 39 or len({case["filename"] for case in cases}) != 39:
        raise ValueError("Independent source material must cover exactly 39 PDFs")
    rows = []
    for case in cases:
        name, number = case["filename"], case["page"]
        source = (corpus / name).resolve()
        if Path(name).name != name or source.parent != corpus.resolve():
            raise ValueError("Unsafe public source path")
        if digest(source) != case["sha256"]:
            raise ValueError("Public source identity changed")
        with fitz.open(source) as native:
            if not 1 <= number <= len(native):
                raise ValueError("Independent physical source page is outside the PDF")
            page = native[number - 1]
            text = page.get_text()
            if case.get("source_kind") != "independent_visual_scan_page" and (
                    normalized(case["quote"]) not in normalized(text)):
                raise ValueError("Independent quote is absent from the unchanged source")
            rows.append({"case_id": case["case_id"], "filename": name,
                         "pdf_sha256": case["sha256"], "page": number,
                         "native_text": text, "question": case["question"],
                         "quote": case["quote"]})
    output.mkdir(parents=True)
    for n, row in enumerate(rows, 1):
        prefix = output / f"source-{n:02d}"
        subprocess.run([str(poppler), "-f", str(row["page"]), "-l", str(row["page"]),
                        "-singlefile", "-r", "100", "-png", str(corpus / row["filename"]),
                        str(prefix)], check=True, capture_output=True)
        png = prefix.with_suffix(".png")
        row.update(image_path=str(png.resolve()), image_sha256=digest(png))
    report = {"evaluation_kind": "independent_complete_gold_page_inspection_material",
              "model_calls": 0, "source_gold_sha256": digest(gold_path),
              "scientific_verdict": "not_assigned_by_renderer", "sources": rows}
    (output / "sources.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return {"model_calls": 0, "complete_source_pages": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-gold", "corpus", "poppler", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.source_gold, args.corpus, args.poppler, args.output_dir)))
