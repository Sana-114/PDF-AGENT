"""Pin all organizer inputs and merge independent bounded parser checks; zero cloud calls."""

import argparse
import hashlib
import json
from pathlib import Path

import fitz

EXTRA_METADATA = {
    "N19-1423.pdf": ("BERT: Pre-training of Deep Bidirectional Transformers", "Jacob Devlin"),
    "language_understanding_paper.pdf": (
        "Improving Language Understanding by Generative Pre-Training", "Alec Radford"),
    "2005.14165v4.pdf": ("Language Models are Few-Shot Learners", "Tom B. Brown"),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(corpus, inventory_path, remaining_manifest, core_manifest, output):
    if output.exists():
        raise ValueError("Never overwrite a source preparation directory")
    inventory = json.loads(inventory_path.read_bytes())
    records = {row["filename"]: row for row in inventory["documents"]}
    if len(records) != 39 or len(records) != len(inventory["documents"]):
        raise ValueError("Expected exactly 39 uniquely inventoried organizer PDFs")
    remaining = json.loads(remaining_manifest.read_bytes())["downloads"]
    core = json.loads(core_manifest.read_bytes())["downloads"]
    entries = {row["filename"]: row for row in [*remaining, *core]}
    if len(entries) != len(remaining) + len(core):
        raise ValueError("Overlapping independent parser manifests")
    for name, (title, author) in EXTRA_METADATA.items():
        record = records[name]
        entries[name] = {
            "filename": name, "sha256": record["sha256"],
            "expect": {"exact_pages": record["pages"], "title_contains": title,
                       "required_authors": [author], "min_outline_nodes": 1,
                       "min_references": 1,
                       "min_extracted_text_chars": int(record["native_text_characters"] * .65),
                       "max_elapsed_seconds": 300, "max_python_peak_memory_mb": 256},
            "baseline_source": "independently read organizer first-page native text",
        }
    if set(entries) != set(records):
        raise ValueError("Every organizer PDF must have an explicit parser expectation")
    # Preserve every previous strict gate. The book's title is an added coverage
    # check, not a claim that all its metadata/structures have been verified.
    book = "UnderstandingDeepLearning_02_09_26_C.pdf"
    entries[book]["expect"]["title_contains"] = "Understanding Deep Learning"
    entries[book]["expect"]["min_extracted_text_chars"] = int(
        records[book]["native_text_characters"] * .95)
    entries[book]["expect"]["max_elapsed_seconds"] = 900
    entries[book]["expect"]["max_python_peak_memory_mb"] = 512
    notes = []
    for name, entry in sorted(entries.items()):
        if Path(name).name != name or entry["sha256"] != records[name]["sha256"]:
            raise ValueError("Unsafe or mismatched independent input identity")
        path = (corpus / name).resolve()
        if path.parent != corpus.resolve() or digest(path) != entry["sha256"]:
            raise ValueError("Source input differs from inventoried fingerprint")
        with fitz.open(path) as pdf:
            if len(pdf) != entry["expect"]["exact_pages"]:
                raise ValueError("Inventoried page count differs")
            text = " ".join(pdf[0].get_text().split())
            if name in EXTRA_METADATA:
                title, author = EXTRA_METADATA[name]
                if title.casefold() not in text.casefold() or author not in text:
                    raise ValueError("Extra metadata gold absent from independent native page")
            notes.append({"filename": name, "sha256": entry["sha256"],
                          "page_count": len(pdf), "first_page_native_text": text})
    output.mkdir(parents=True)
    manifest = {
        "schema_version": "1.0", "origin": "user-provided-organizer-all-39",
        "scope": "Complete page coverage and explicit bounded metadata/structure gates; "
                 "not all cells/formulas/affiliations or six-module acceptance",
        "input_manifest_sha256": {str(path): digest(path) for path in (
            inventory_path, remaining_manifest, core_manifest)},
        "downloads": [entries[name] for name in sorted(entries)],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    (output / "source-notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    return {"documents": 39, "model_calls": 0,
            "pages": sum(record["pages"] for record in records.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "inventory", "remaining-manifest", "core-manifest", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.corpus, args.inventory, args.remaining_manifest,
                             args.core_manifest, args.output_dir)))
