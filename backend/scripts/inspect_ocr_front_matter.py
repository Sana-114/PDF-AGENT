"""SHA-pinned, read-only crop OCR trials; do not replace parser output or gold."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pdf", type=Path, required=True)
    cli.add_argument("--sha256", required=True)
    cli.add_argument("--clip", nargs=4, type=float, required=True)
    cli.add_argument("--languages", nargs="+", default=["eng", "eng+pol", "pol+eng"])
    cli.add_argument("--dpi", nargs="+", type=int, default=[300, 450])
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists() or hashlib.sha256(args.pdf.read_bytes()).hexdigest() != args.sha256:
        raise ValueError("Output exists or source SHA mismatch")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"source_sha256": args.sha256, "clip": args.clip, "page": 1,
              "llm_calls": 0, "trials": []}
    with fitz.open(args.pdf) as document:
        page = document[0]
        clip = fitz.Rect(args.clip)
        if not page.rect.contains(clip) or clip.is_empty:
            raise ValueError("Invalid crop bounds")
        for dpi in args.dpi:
            if not 72 <= dpi <= 600:
                raise ValueError("Diagnostic OCR resolution must be between 72 and 600")
            pixmap = page.get_pixmap(clip=clip, dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            for language in args.languages:
                payload = pixmap.pdfocr_tobytes(language=language,
                                               tessdata=settings.ocr_tessdata or None)
                with fitz.open("pdf", payload) as cropped:
                    text = cropped[0].get_text("text")
                trial = {"languages": language, "dpi": dpi, "text": text}
                report["trials"].append(trial)
                print(json.dumps(trial, ensure_ascii=False), flush=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
