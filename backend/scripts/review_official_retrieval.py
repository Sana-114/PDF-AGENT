"""Recheck saved retrieval evidence without parsing, indexing, or model requests."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.agent import EvidenceAnchor  # noqa: E402
from scripts.evaluate_official_batch import file_hash  # noqa: E402
from scripts.evaluate_official_retrieval import anchor_valid, probe_hit  # noqa: E402


def review(source: Path, output: Path, parse_report: Path,
           override_report: Path | None = None) -> dict:
    if output.exists():
        raise ValueError("Never overwrite a prior report")
    original = json.loads(source.read_text(encoding="utf-8"))
    if original.get("status") != "completed":
        raise ValueError("Only a complete saved retrieval run may be reviewed")
    # The retrieval runner reserialized AST copies without indentation. Verify
    # their contents against the byte-hashed original parser artifacts, rather
    # than confusing JSON whitespace changes with changed evidence.
    records = {}
    for kind, report_path in [("parse_report", parse_report), ("override_report", override_report)]:
        if kind not in original["inputs_sha256"]:
            if report_path:
                raise ValueError("Unrecorded parser correction report")
            continue
        if not report_path or file_hash(report_path) != original["inputs_sha256"][kind]:
            raise ValueError("Original parser report SHA differs")
        parser_result = json.loads(report_path.read_text(encoding="utf-8"))
        for record in parser_result["documents"]:
            job = record["artifacts_directory"]
            if Path(job).name != job:
                raise ValueError("Unsafe source AST directory")
            original_ast = (report_path.parent / job / "artifacts/parsed.json").resolve()
            if not original_ast.is_relative_to(report_path.parent.resolve()):
                raise ValueError("Source AST outside parser report directory")
            records[record["filename"]] = original_ast
    asts = {}
    for doc in original["documents"]:
        document_id = doc["document_id"]
        if Path(document_id).name != document_id:
            raise ValueError("Unsafe saved document ID")
        path = (source.parent / "parsed" / f"{document_id}.json").resolve()
        if not path.is_relative_to(source.parent.resolve()):
            raise ValueError("AST path outside saved test directory")
        original_ast = records[doc["filename"]]
        if file_hash(original_ast) != doc["ast_sha256"]:
            raise ValueError("Original source AST changed since the actual model run")
        ast = json.loads(path.read_text(encoding="utf-8"))
        if ast != json.loads(original_ast.read_text(encoding="utf-8")):
            raise ValueError("Saved AST contents differ from the verified original")
        asts[doc["filename"]] = (document_id, ast)
    cases = []
    for old in original["cases"]:
        case = {**old, "original_status": old["status"],
                "original_failures": old["failures"], "failures": []}
        evidence = [EvidenceAnchor.model_validate(e) for e in old.get("evidence", [])]
        if not any(probe_hit(e, old["gold"]) for e in evidence):
            case["failures"].append("gold_quote_not_in_final_top_k_on_source_page")
        if not evidence or any(e.retrieval_mode != "reranked" or e.recall_mode != "hybrid"
                               for e in evidence):
            case["failures"].append("not_real_hybrid_reranked_chain")
        document_id, ast = asts[old["filename"]]
        if any(not anchor_valid(e, document_id, ast) for e in evidence):
            case["failures"].append("invalid_or_out_of_scope_source_anchor")
        case["failures"].extend(f for f in old["failures"] if f.startswith("retrieval_error:"))
        case["status"] = "failed" if case["failures"] else "passed"
        cases.append(case)
    report = {"evaluation_kind": "saved_bge_evidence_anchor_recheck_not_new_model_run",
              "original_report_sha256": file_hash(source),
              "parse_report_sha256": file_hash(parse_report),
              "validator_sha256": file_hash(Path(__file__).with_name(
                  "evaluate_official_retrieval.py")),
              "model_calls": 0, "embedding_calls": 0, "reranker_calls": 0, "parser_calls": 0,
              "vector_index_modified": False, "original_summary": original["summary"],
              "summary": {"cases": len(cases),
                          "passed": sum(c["status"] == "passed" for c in cases),
                          "failed": sum(c["status"] == "failed" for c in cases)},
              "cases": cases}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parse-report", type=Path, required=True)
    parser.add_argument("--override-report", type=Path)
    args = parser.parse_args()
    result = review(args.report, args.output, args.parse_report, args.override_report)
    print(json.dumps(result["summary"]))
    raise SystemExit(0 if not result["summary"]["failed"] else 1)


if __name__ == "__main__":
    main()
