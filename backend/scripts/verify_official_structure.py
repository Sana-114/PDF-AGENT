"""Reparse official PDFs and grade selected cells/equations against their printed pages."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.parsers.registry import get_parser  # noqa: E402

SCAN = "1706.03762v1_img.pdf"
BOOK = "UnderstandingDeepLearning_02_09_26_C.pdf"
VECTOR = "1706.03762v1.pdf"
# Printed Table 2, English-German and English-French columns including empty cells.
BLEU = {
    "ByteNet": ("23.75", ""), "Deep-Att + PosUnk": ("", "39.2"),
    "GNMT + RL": ("24.6", "39.92"), "ConvS2S": ("25.16", "40.46"),
    "MoE": ("26.03", "40.56"), "Deep-Att + PosUnk Ensemble": ("", "40.4"),
    "GNMT + RL Ensemble": ("26.30", "41.16"), "ConvS2S Ensemble": ("26.36", "41.29"),
    "Transformer (base model)": ("27.3", "38.1"), "Transformer (big)": ("28.4", "41.0"),
}


def table_rows(doc, page, caption):
    tables = [t for t in doc["tables"] if t["page_number"] == page
              and (t.get("caption") or "").startswith(caption)]
    return tables[0]["rows"] if len(tables) == 1 else []


def scan_checks(doc):
    rows = table_rows(doc, 8, "Table 2:")
    checks = []
    for name, values in BLEU.items():
        hits = [row for row in rows if row and re.sub(r"\s*\[\d+\]\s*$", "", row[0]) == name]
        for index, expected in enumerate(values, 1):
            actual = hits[0][index] if len(hits) == 1 and len(hits[0]) > index else None
            checks.append({"page": 8, "model": name, "column": ["EN-DE", "EN-FR"][index - 1],
                           "expected": expected, "actual": actual, "passed": actual == expected})
    rows3 = table_rows(doc, 9, "Table 3:")
    base = [row for row in rows3 if row and row[0].strip() == "base"]
    expected_base = ["base", "6", "512", "1024", "8", "64", "64", "0.1", "0.1", "100K",
                     "4.92", "25.8", "65"]
    formulas = [f["latex"] for f in doc["formulas"] if f["page_number"] == 3]
    parser_rows = [r for r in table_rows(doc, 9, "Table 4:")
                   if r and r[0].strip() == "Transformer (4 layers)"]
    expected_training = [["WSJ only, discriminative", "91.3"], ["semi-supervised", "92.7"]]
    observed_training = [r[1:3] for r in parser_rows]
    base_cost = [r[3:] for r in rows if r and r[0] == "Transformer (base model)"]
    canonical_cost = re.compile(r"3\.3[×·*]10(?:\^\{?18\}?|¹⁸)")
    return {"table2_bleu_cells": checks,
            "table2_base_flops": {"page": 8, "expected": "3.3 × 10^18", "actual": base_cost,
                                  "passed": len(base_cost) == 1 and any(canonical_cost.fullmatch(
                                      cell.replace(" ", "")) for cell in base_cost[0])},
            "table4_context_and_f1": {"page": 9, "expected": expected_training,
                                      "actual": observed_training,
                                      "passed": observed_training == expected_training},
            "table3_base_row": {"page": 9, "expected": expected_base, "actual": base,
                                "passed": base == [expected_base]},
            "attention_formula": {"page": 3, "actual": formulas,
                                  "criterion": "one candidate retains softmax, transposed K, "
                                               "fraction, square root and dk subscript",
                                  "passed": any(all(t in f for t in (
                                      "softmax", "^{T}", r"\frac", r"\sqrt", "_{k}"))
                                      for f in formulas)}}


def book_checks(doc):
    checks = []
    for page, number, expected in [
        (31, "2.1", "y=f[x]."), (32, "2.2", r"y=f[x,\phi]."),
        (32, "2.4", r"y=f[x,\phi]=\phi_{0}+\phi_{1}x."),
    ]:
        hits = [f for f in doc["formulas"] if f["page_number"] == page and
                f["latex"].endswith(f"({number})")]
        actual = hits[0]["latex"] if len(hits) == 1 else ""
        checks.append({"page": page, "number": number, "expected": expected, "actual": actual,
                       "passed": actual.replace(" ", "") == f"{expected}({number})"})
    fragments = [f["latex"] for f in doc["formulas"] if f["page_number"] == 33]
    return {"single_block_equations": checks, "least_squares_2.5": {
        "page": 33, "actual": fragments, "criterion": "one numbered expression linking L to "
        "a sum with lower index and squared residual", "passed": any(all(
            t in f for t in ["(2.5)", "L[", r"\sum", "_{", "^{2}"]) for f in fragments)}}


def dense_row_controls(doc):
    """Separate from the original 32 gates: validate sparse rows away from the base row."""
    rows = table_rows(doc, 9, "Table 3:")
    checks = []
    for heads, key_dim, ppl, bleu in [("1", "512", "5.29", "24.9"), ("4", "128", "5.00", "25.5"),
                                      ("16", "32", "4.91", "25.8"), ("32", "16", "5.01", "25.4")]:
        hits = [row for row in rows if len(row) == 13 and row[4:7] == [heads, key_dim, key_dim]]
        for col, expected in [(10, ppl), (11, bleu)]:
            actual = hits[0][col] if len(hits) == 1 else None
            checks.append({"page": 9, "heads": heads, "metric": "PPL" if col == 10 else "BLEU",
                           "expected": expected, "actual": actual, "passed": actual == expected})
    return checks


def vector_checks(doc, path):
    checks = []
    with fitz.open(path) as pdf:
        for page, caption, model, col, value in [
            (8, "Table 2:", "ConvS2S Ensemble", 1, "26.36"),
            (8, "Table 2:", "Transformer (big)", 1, "28.4"),
            (8, "Table 2:", "Transformer (big)", 2, "41.0"),
            (9, "Table 4:", "Transformer (4 layers)", 2, "91.3"),
        ]:
            boxes = [cell["bbox"] for t in doc["tables"] if t["page_number"] == page
                     and (t["caption"] or "").startswith(caption) for cell in t["cells"]
                     if cell["column"] == col and cell["text"] == value
                     and t["rows"][cell["row"]][0].startswith(model) and cell["bbox"]]
            clip = (pdf[page - 1].get_textbox(fitz.Rect(boxes[0])).strip()
                    if len(boxes) == 1 else None)
            checks.append({"page": page, "table": caption, "model": model, "expected": value,
                           "text_at_source_anchor": clip, "bbox": boxes, "passed": clip == value})
    return {"cell_anchors": checks}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--corpus", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((ROOT / "docs/official-pdf-manifest.json").read_bytes())["downloads"]
    report = {"scope": "selected page-level checks; no all-corpus accuracy claim", "llm_calls": 0,
              "files": []}
    for filename in (SCAN, BOOK, VECTOR):
        path = args.corpus / filename
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = next(e for e in manifest if e["filename"] == filename)
        if sha != entry["sha256"]:
            raise ValueError(f"Wrong PDF identity: {filename}")
        parser = get_parser(str(path))
        parsed = parser.parse(str(path), batch_size=25, source_fingerprint=sha,
                              checkpoint_dir=out / (path.stem + "-checkpoint"))
        doc = parsed.to_dict()
        if filename == SCAN:
            report["additional_sparse_cell_checks"] = dense_row_controls(doc)
        if filename == VECTOR:
            checks = vector_checks(doc, path)
        else:
            checks = (scan_checks if filename == SCAN else book_checks)(doc)
        report["files"].append({"filename": filename, "sha256": sha, "parser": parser.version,
                                "pages": len(parsed.pages),
                                "expected_pages": entry["expect"]["exact_pages"],
                                "tables": len(parsed.tables), "formulas": len(parsed.formulas),
                                "checks": checks})
    gates = [item["passed"] for f in report["files"] for group in f["checks"].values()
             for item in (group if isinstance(group, list) else [group])]
    ok = all(gates) and all(f["pages"] == f["expected_pages"] for f in report["files"])
    ok = ok and all(c["passed"] for c in report["additional_sparse_cell_checks"])
    report["status"] = "passed" if ok else "partial"
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    print(f"{report['status']}: {sum(gates)}/{len(gates)} selected content checks; {out}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
