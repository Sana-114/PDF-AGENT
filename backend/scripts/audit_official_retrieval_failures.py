"""Inspect saved failures against native PDF pages; never call a model or alter scores."""

import argparse
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.agent import EvidenceAnchor  # noqa: E402
from app.services.chunking import build_chunk_drafts  # noqa: E402
from scripts.evaluate_official_batch import file_hash  # noqa: E402
from scripts.evaluate_official_retrieval import normalized, source_probe  # noqa: E402


def audit(corpus: Path, source: Path, output: Path, case_ids: list[str]) -> dict:
    if output.exists():
        raise ValueError("Never overwrite saved source audit evidence")
    report = json.loads(source.read_text(encoding="utf-8"))
    available = {c["case_id"]: c for c in report["cases"]}
    if not case_ids or len(set(case_ids)) != len(case_ids) or set(case_ids) - available.keys():
        raise ValueError("Unknown, empty, or duplicate explicit case selection")
    records = {d["filename"]: d for d in report["documents"]}
    output.mkdir(parents=True)
    cases = []
    for n, key in enumerate(case_ids, 1):
        old = available[key]
        name = old["filename"]
        record = records[name]
        pdf = corpus / name
        if pdf.resolve().parent != corpus.resolve() or file_hash(pdf) != record["sha256"]:
            raise ValueError("Source PDF differs or resolves outside the corpus")
        ast_path = source.parent / "parsed" / f"{record['document_id']}.json"
        if not ast_path.resolve().is_relative_to((source.parent / "parsed").resolve()):
            raise ValueError("Unsafe saved AST path")
        ast = json.loads(ast_path.read_text(encoding="utf-8"))
        chunks = build_chunk_drafts(ast)
        gold = old["gold"]
        gold_chunks = [vars_for_chunk(c) for c in chunks
                       if c.page_number == gold["page_number"]
                       and normalized(gold["quote"]) in normalized(c.text)]
        evidence = [EvidenceAnchor.model_validate(e) for e in old.get("evidence", [])]
        # Full pages, not cropped quotes: reviewers need headings, tables and footnotes.
        pages = list(dict.fromkeys([gold["page_number"],
                                   *[e.page_number for e in evidence[:3]]]))
        with fitz.open(pdf) as native:
            checked = source_probe(native, gold["page_number"], gold["quote"])
            inspected = []
            for page_number in pages:
                image = f"{n:02d}-page-{page_number}.png"
                preview = native[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
                preview.save(output / image)
                inspected.append({"page_number": page_number, "preview": image,
                                  "native_text": native[page_number - 1].get_text()})
        cases.append({"case_id": key, "question": old["question"], "gold": checked,
                      "original_candidate_hit": old.get("candidate_hit"),
                      "original_final_hit": old.get("final_hit"),
                      "gold_chunks": gold_chunks, "pages": inspected,
                      "saved_evidence": old.get("evidence", [])})
    result = {"evaluation_kind": "native_source_failure_audit_not_retrieval_or_generation",
              "source_report_sha256": file_hash(source), "model_calls": 0,
              "parser_calls": 0, "vector_index_modified": False,
              "cases": cases}
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return {"cases": len(cases), "gold_present_in_current_chunks": sum(
        bool(c["gold_chunks"]) for c in cases), "model_calls": 0}


def vars_for_chunk(chunk) -> dict:
    return {"page_number": chunk.page_number, "section": chunk.section,
            "block_ids": chunk.block_ids, "bbox": chunk.bbox, "text": chunk.text}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-ids", nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.corpus, args.report, args.output_dir, args.case_ids)))


if __name__ == "__main__":
    main()
