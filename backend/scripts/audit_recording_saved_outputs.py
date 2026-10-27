"""Audit saved recording outputs without a database, network or model call.

Checks literal AST provenance and file identities, not independent scientific
entailment. Does not rewrite any previous acceptance result.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return " ".join(text.split())


def source_pdf(root, row):
    # Worker uploads have a storage UUID independent of the document UUID.
    # Match immutable bytes, never a title or a similar filename.
    candidates = [path for path in (root / "data/uploads").glob("*.pdf")
                  if digest(path) == row["source_sha256"]]
    if len(candidates) != 1:
        raise ValueError(f"Expected one SHA-matched PDF for {row['document_id']}")
    return candidates[0]


def literal_anchor(source, anchor):
    page = next((p for p in source["pages"] if p["page_number"] == anchor["page_number"]), None)
    if page is None:
        return False
    blocks = {b["block_id"]: b for b in page["blocks"]}
    ids = anchor["block_ids"]
    quote = normalized(anchor["quote"])
    if not ids or not quote:
        return False
    if all(b in blocks for b in ids):
        return quote in normalized(" ".join(blocks[b].get("text", "") for b in ids))
    for kind, collection, key in (("table", "tables", "table_id"), ("figure", "figures", "figure_id")):
        if anchor["source_type"] != kind:
            continue
        for item in source.get(collection, []):
            caption_id = item.get("caption_block_id")
            if (item["page_number"] != anchor["page_number"] or caption_id not in blocks
                    or set(ids) != {item[key], caption_id}):
                continue
            if normalized(item.get("caption", "")) != normalized(blocks[caption_id]["text"]):
                continue
            literal = item["caption"] + ("\n" + item.get("markdown", "") if kind == "table" else "")
            return quote == normalized(literal)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    root, output = args.run.resolve(), args.output.resolve()
    if not all(p.is_relative_to(backend / "tmp") for p in (root, output)):
        raise ValueError("Only workspace acceptance artifacts are allowed")
    if output.exists():
        raise ValueError("Never overwrite prior evidence")
    rows = json.loads((root / "data/fixture.json").read_text(encoding="utf-8"))
    parsed, identities, errors = {}, [], []
    for row in rows:
        document_id = row["document_id"]
        if Path(document_id).name != document_id:
            raise ValueError("Invalid fixture ID")
        pdf = source_pdf(root, row)
        ast = root / "data/parsed" / f"{document_id}.json"
        checks = {"pdf": digest(pdf) == row["source_sha256"],
                  "ast": digest(ast) == row["ast_sha256"]}
        identities.append({"filename": row["filename"], "storage_key": pdf.name, **checks})
        if not all(checks.values()):
            errors.append(f"Changed fixture: {row['filename']}")
        parsed[document_id] = json.loads(ast.read_text(encoding="utf-8"))
    generation_path = root / "generation-browser.json"
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    audits = []
    for case in generation["cases"]:
        response = case["response"]
        cited = {eid for claim in response["claims"] for eid in claim["evidence_ids"]}
        for anchor in response["evidence"]:
            source = parsed[anchor["document_id"]]
            literal = literal_anchor(source, anchor)
            item = {"case": case["case_id"], "evidence_id": anchor["evidence_id"],
                    "document_id": anchor["document_id"], "page": anchor["page_number"],
                    "cited": anchor["evidence_id"] in cited, "literal_ast_match": literal}
            audits.append(item)
            if not literal:
                errors.append(f"Nonliteral anchor: {case['case_id']} {anchor['evidence_id']}")
        if not cited.issubset({e["evidence_id"] for e in response["evidence"]}):
            errors.append("Unknown claim citation")
    spec = importlib.util.spec_from_file_location(
        "publication_metadata_audit", backend / "app/services/publication_metadata.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bert = next(row for row in rows if row["filename"] == "N19-1423.pdf")
    imprint = module.publication_footer(parsed[bert["document_id"]])
    if not imprint or imprint["year"] != 2019 or imprint["venue"] != "NAACL-HLT":
        errors.append("Official BERT imprint not recovered")
    translation_path = root / "translation-browser.json"
    translation = json.loads(translation_path.read_text(encoding="utf-8"))["cases"][0]["response"]
    text = translation["translation"]
    preserved = all(value in text for value in ("$y=x^2$", "`beta_2 = 0.98`", "[11]"))
    export = (root / "translation-browser/synthetic-translation.md").read_text(encoding="utf-8")
    if not preserved or text not in export:
        errors.append("Translation or export integrity failed")
    records = sorted((p.relative_to(backend).as_posix(), digest(p))
                     for p in (backend / "app").rglob("*.py"))
    source_hash = hashlib.sha256("".join(f"{p}\0{sha}\n" for p, sha in records).encode()).hexdigest()
    report = {"status": "failed" if errors else "passed", "cloud_calls": 0,
              "scope": "Saved artifact integrity and literal AST anchors; not a live runtime or scientific accuracy score",
              "source_app_files": len(records), "source_app_sha256": source_hash,
              "input_hashes": {p.name: digest(p) for p in (generation_path, translation_path)},
              "identities": identities, "anchor_audit": audits,
              "bert_footer": imprint, "translation_protected_strings": preserved,
              "translation_export_identical": text in export, "errors": errors}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "files": len(rows), "anchors": len(audits),
                      "errors": errors, "source_app_sha256": source_hash}))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
