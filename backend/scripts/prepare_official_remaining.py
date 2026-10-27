"""Prepare bounded native-PDF checks and page previews without the production parser or LLM."""

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import fitz
from PIL import Image, ImageDraw


def normalize(text: str) -> str:
    return " ".join(text.split())


def prepare(corpus: Path, inventory_path: Path, metadata_path: Path, output: Path) -> dict:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    records = {item["filename"]: item for item in inventory["documents"]}
    selected = metadata["documents"]
    names = [item["filename"] for item in selected]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate selected filenames")
    expected = set(records) - set(metadata["excluded_previously_tested"])
    if set(names) != expected:
        raise ValueError("Selection must exactly match the remaining inventoried corpus")
    # Hash-check every source before creating an output directory.
    for name in names:
        path = corpus / name
        if Path(name).name != name:
            raise ValueError("Unsafe filename")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != records[name]["sha256"]:
            raise ValueError(f"Source fingerprint mismatch: {name}")
    output.mkdir(parents=True, exist_ok=False)
    previews = output / "previews"
    previews.mkdir()
    manifest_items = []
    source_notes = []
    tiles = []
    for index, item in enumerate(selected, 1):
        name = item["filename"]
        record = records[name]
        with fitz.open(corpus / name) as document:
            first = document[0]
            first_text = first.get_text("text")
            if normalize(item["title_contains"]).casefold() not in normalize(first_text).casefold():
                raise ValueError(f"Metadata title is not in the first-page native source: {name}")
            if item["author"] not in normalize(first_text):
                raise ValueError(f"Metadata author is not in the first-page native source: {name}")
            if "authors_exact" in item:
                authors = item["authors_exact"]
                if not authors or len(authors) != len(set(authors)):
                    raise ValueError("Exact author gold must be nonempty and unique")
                def compact(value):
                    return re.sub(r"[\s\d*∗†‡⋄♦§¶♠♣♥♢]+", "",
                                  unicodedata.normalize("NFKC", value))
                if any(compact(author) not in compact(first_text) for author in authors):
                    raise ValueError(f"Exact author gold absent from first-page source: {name}")
            # Independently choose a reference-page fragment, never from parsed references.
            reference_probe = None
            reference_page = None
            for page in document:
                lines = [normalize(line) for line in page.get_text("text").splitlines()]
                heading = next(
                    (i for i, line in enumerate(lines)
                     if re.fullmatch(r"(?:\d+\.?\s*)?(?:References|Bibliography)", line,
                                     flags=re.IGNORECASE)), None,
                )
                if heading is None:
                    continue
                reference_page = page.number + 1
                candidate = next((line for line in lines[heading + 1:]
                                  if len(line) >= 35 and "http" not in line), None)
                if candidate:
                    reference_probe = {"page_number": reference_page, "contains": candidate}
                break
            full_png = previews / f"{index:02d}-first-page.png"
            first.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(full_png)
            # Compact contact sheet is a navigation aid; retain complete page images.
            crop = fitz.Rect(0, 0, first.rect.width, min(first.rect.height, 310))
            pixmap = first.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), clip=crop, alpha=False)
            tile = Image.new("RGB", (1120, 600), "white")
            rendered = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            rendered.thumbnail((1120, 555))
            tile.paste(rendered, (0, 40))
            ImageDraw.Draw(tile).text((8, 8), f"{index:02d} {name}", fill="black")
            tiles.append(tile)
            if reference_page is not None:
                document[reference_page - 1].get_pixmap(
                    matrix=fitz.Matrix(1.2, 1.2), alpha=False,
                ).save(previews / f"{index:02d}-references-page.png")
            expect = {
                "exact_pages": record["pages"],
                "title_contains": item["title_contains"],
                "required_authors": [item["author"]],
                "min_extracted_text_chars": max(100, int(record["native_text_characters"] * .65)),
                "min_outline_nodes": 1,
                "min_references": 1,
                "max_elapsed_seconds": 300,
                "max_python_peak_memory_mb": 256,
            }
            if reference_probe:
                expect["reference_samples"] = [reference_probe]
            if "authors_exact" in item:
                expect["authors_exact"] = item["authors_exact"]
            manifest_items.append({
                "filename": name, "sha256": record["sha256"], "expect": expect,
                "baseline_source": "organizer inventory + separately transcribed "
                                   "first-page metadata",
                "reference_probe_review": "native-source-derived; visual semantic review pending",
            })
            source_notes.append({
                "filename": name, "page_count": len(document),
                "first_page_text": first_text, "reference_probe": reference_probe,
            })
    for offset in range(0, len(tiles), 8):
        sheet = Image.new("RGB", (2240, 2400), "#eeeeee")
        for position, tile in enumerate(tiles[offset:offset + 8]):
            sheet.paste(tile, ((position % 2) * 1120, (position // 2) * 600))
        sheet.save(previews / f"metadata-sheet-{offset // 8 + 1}.png")
    manifest = {
        "schema_version": "1.0", "origin": "official-remaining-32",
        "acceptance_scope": "bounded_metadata_page_coverage_and_source_probe_checks",
        "gold_note": "One credited author/team and title fragment are checked for all files; "
        "exact ordered author lists are checked only where explicitly supplied. References "
        "are independently sampled from native PDF text, not from parser outputs. Counts do not "
        "establish table/formula accuracy, and passing is not full six-module acceptance.",
        "excluded_previously_tested": metadata["excluded_previously_tested"],
        "downloads": manifest_items,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (output / "source-notes.json").write_text(
        json.dumps(source_notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return {"documents": len(names), "pages": sum(records[name]["pages"] for name in names),
            "manifest": str(output / "manifest.json"), "model_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.corpus, args.inventory, args.metadata, args.output_dir)))


if __name__ == "__main__":
    main()
