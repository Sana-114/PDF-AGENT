"""Read-only OCR page/word/table diagnostics; inputs are never modified."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.parsers.layout import extract_text_blocks  # noqa: E402
from app.parsers.raster_layout import detect_raster_layout  # noqa: E402
from app.parsers.text_table import extract_borderless_tables  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--pages", nargs="+", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or hashlib.sha256(args.pdf.read_bytes()).hexdigest() != args.sha256:
        raise ValueError("Report exists or source SHA mismatch")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"source_sha256": args.sha256, "llm_calls": 0, "pages": []}
    with fitz.open(args.pdf) as document:
        for number in args.pages:
            print(f"Inspecting OCR page {number}", flush=True)
            page = document[number - 1]
            textpage = page.get_textpage_ocr(language=settings.ocr_languages, dpi=settings.ocr_dpi,
                                           full=True, tessdata=settings.ocr_tessdata or None)
            raw = extract_text_blocks(page.get_text("dict", textpage=textpage), number)
            grid, _ = detect_raster_layout(page, textpage=textpage, raw_blocks=raw)
            borderless = extract_borderless_tables(page, raw, textpage=textpage)
            image = args.output.parent / f"source-page-{number}.png"
            page.get_pixmap(dpi=150, alpha=False).save(image)
            report["pages"].append({
                "page": number, "source_image": str(image),
                "blocks": [{"text": block.text, "bbox": block.bbox} for block in raw],
                "grid_tables": [{"bbox": table.bbox, "rows": table.rows} for table in grid],
                "borderless_tables": [
                    {"bbox": table.bbox, "rows": table.rows} for table in borderless
                ],
            })
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"page": number, "grid": len(grid), "borderless": len(borderless)}),
                  flush=True)
