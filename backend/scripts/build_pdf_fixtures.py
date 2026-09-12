import argparse
import shutil
from pathlib import Path

import fitz


def build_fixtures(
    source: Path,
    output_dir: Path,
    *,
    scan_pages: int = 6,
    scan_dpi: int = 144,
    long_pages: int = 541,
) -> dict[str, Path]:
    if not source.is_file():
        raise FileNotFoundError(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    native_path = output_dir / "local_chinese_thesis_native.pdf"
    scan_path = output_dir / f"local_chinese_thesis_scan_{scan_pages}p.pdf"
    long_path = output_dir / f"local_chinese_thesis_long_{long_pages}p.pdf"
    shutil.copy2(source, native_path)
    _build_scan(source, scan_path, page_limit=scan_pages, dpi=scan_dpi)
    _build_long_document(source, long_path, target_pages=long_pages)
    return {"native": native_path, "scan": scan_path, "long": long_path}


def _build_scan(source: Path, output: Path, *, page_limit: int, dpi: int) -> None:
    source_pdf = fitz.open(source)
    scanned_pdf = fitz.open()
    try:
        for source_page in list(source_pdf)[:page_limit]:
            target = scanned_pdf.new_page(
                width=source_page.rect.width,
                height=source_page.rect.height,
            )
            pixmap = source_page.get_pixmap(dpi=dpi, alpha=False)
            target.insert_image(target.rect, stream=pixmap.tobytes("jpeg", jpg_quality=82))
        scanned_pdf.save(output, deflate=True, garbage=4)
    finally:
        scanned_pdf.close()
        source_pdf.close()
    with fitz.open(output) as check:
        if len(check) != min(page_limit, _page_count(source)):
            raise RuntimeError("扫描版页数校验失败。")
        if any(page.get_text("text").strip() for page in check):
            raise RuntimeError("扫描版不应包含原生文本层。")


def _build_long_document(source: Path, output: Path, *, target_pages: int) -> None:
    if target_pages < 1:
        raise ValueError("target_pages must be positive")
    source_pdf = fitz.open(source)
    long_pdf = fitz.open()
    try:
        while len(long_pdf) < target_pages:
            remaining = target_pages - len(long_pdf)
            final_page = min(len(source_pdf), remaining) - 1
            long_pdf.insert_pdf(source_pdf, from_page=0, to_page=final_page)
        long_pdf.save(output, deflate=True, garbage=4)
    finally:
        long_pdf.close()
        source_pdf.close()
    if _page_count(output) != target_pages:
        raise RuntimeError("长文档页数校验失败。")


def _page_count(path: Path) -> int:
    with fitz.open(path) as document:
        return len(document)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build scan and long-PDF regression fixtures.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--scan-pages", type=int, default=6)
    parser.add_argument("--scan-dpi", type=int, default=144)
    parser.add_argument("--long-pages", type=int, default=541)
    args = parser.parse_args()
    outputs = build_fixtures(
        args.source,
        args.output_dir,
        scan_pages=args.scan_pages,
        scan_dpi=args.scan_dpi,
        long_pages=args.long_pages,
    )
    for kind, path in outputs.items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
