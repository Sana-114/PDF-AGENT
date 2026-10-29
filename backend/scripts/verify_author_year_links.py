"""Read-only corpus audit; source/AST hashes and independent examples, no LLM."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.document_content import reference_links  # noqa: E402

# Manually selected bibliographic identities, not expectations derived from labels.
CASES = {
    "1607.06450v1.pdf": [("Krizhevsky et al., 2012", "Imagenet"),
                        ("Hinton et al., 2012", "Deep neural networks")],
    "1910.10683v4.pdf": [("Mikolov et al., 2013b", "Distributed representations"),
                        ("Pennington et al., 2014", "GloVe")],
    "2002.04745v2.pdf": [("Vaswani et al., 2017", "Atten"),
                        ("Ba et al., 2016", "Layer normaliza")],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve earlier reports; choose a new output")
    sources, checks = [], []
    rows = json.loads((args.fixture / "fixture.json").read_text(encoding="utf-8"))
    for row in rows:
        ast = args.fixture / "parsed" / (row["document_id"] + ".json")
        pdf = args.fixture / "uploads" / (row["document_id"] + ".pdf")
        assert sha(ast) == row["ast_sha256"] and sha(pdf) == row["source_sha256"]
        parsed = json.loads(ast.read_text(encoding="utf-8"))
        refs, mentions = reference_links(parsed)
        author_year = [m for m in mentions if m["citation_id"].startswith("cite-ay-")]
        ref_by_label = {r["label"]: r for r in refs}
        blocks = {(p["page_number"], b["block_id"]): b for p in parsed["pages"]
                  for b in p["blocks"]}
        for m in author_year:
            block = blocks[m["page_number"], m["block_id"]]
            assert m["citation_text"] == block["text"][m["start_offset"]:m["end_offset"]]
            assert m["label"] in ref_by_label
        for citation, title in CASES.get(row["filename"], []):
            found = [m for m in author_year if " ".join(m["citation_text"].split()) == citation]
            passed = bool(found) and all(
                title.casefold() in ref_by_label[m["label"]]["text"].casefold() for m in found
            )
            checks.append({"filename": row["filename"], "citation": citation,
                           "expected_title_fragment": title, "passed": passed,
                           "source_pages": sorted({m["page_number"] for m in found})})
        sources.append({**row, "reference_count": len(refs),
                        "author_year_count": len(author_year),
                        "synthetic_label_links": sum(m["label"].startswith("author-year-")
                                                     for m in author_year),
                        "numeric_or_key_mentions": len(mentions) - len(author_year),
                        "examples": [{**m, "reference": ref_by_label[m["label"]]}
                                     for m in author_year[:3]]})
    assert len(checks) == sum(map(len, CASES.values()))
    report = {"status": "passed" if all(c["passed"] for c in checks) else "failed",
              "scope": "39 saved official ASTs; link coverage is not full citation accuracy",
              "cloud_calls": 0, "new_parses": 0, "sources": sources, "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "files": len(sources),
                      "files_with_author_year_links": sum(
                          s["author_year_count"] > 0 for s in sources),
                      "links": sum(s["author_year_count"] for s in sources),
                      "independent_checks": checks}, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
