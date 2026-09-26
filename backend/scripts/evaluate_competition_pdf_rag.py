"""Build isolated indexes from source PDFs and score reproducible evidence gold."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import Base  # noqa: E402
from app.evaluation import RetrievalEvaluationSet, evaluate_retrieval  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402, F401
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.parsers.registry import get_parser  # noqa: E402
from app.services.chunking import replace_document_chunks  # noqa: E402
from app.services.retrieval import LexicalRetriever  # noqa: E402


def source_files(dataset: RetrievalEvaluationSet) -> list[str]:
    filenames = []
    for case in dataset.cases:
        if len(case.documents) != 1 or not case.documents[0].filename:
            raise ValueError(f"{case.case_id}: exactly one filename selector is required")
        filename = case.documents[0].filename
        if Path(filename).name != filename:
            raise ValueError(f"{case.case_id}: filename must not contain a directory")
        filenames.append(filename)
    return list(dict.fromkeys(filenames))


def verified_pdf(path: Path, manifest: dict[str, dict]) -> str:
    entry = manifest.get(path.name)
    if entry is None or not entry.get("sha256"):
        raise ValueError(f"Missing pinned SHA-256 for {path.name}")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"Invalid PDF header: {path.name}")
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != entry["sha256"].casefold():
        raise ValueError(f"SHA-256 mismatch: {path.name}")
    return digest


def comparison_groups(raw: dict, case_results: list[dict]) -> list[dict]:
    by_id = {item["case_id"]: item for item in case_results}
    groups = []
    for group in raw.get("comparison_groups", []):
        case_ids = group["case_ids"]
        if len(case_ids) < 2 or len(case_ids) != len(set(case_ids)):
            raise ValueError(f"{group['group_id']}: requires distinct comparison cases")
        if any(case_id not in by_id for case_id in case_ids):
            raise ValueError(f"{group['group_id']}: references an unknown case")
        sources = [by_id[case_id]["document_ids"][0] for case_id in case_ids]
        if len(set(sources)) != len(sources):
            raise ValueError(f"{group['group_id']}: cases must use different PDFs")
        groups.append(
            {
                "group_id": group["group_id"],
                "case_ids": case_ids,
                "passed": all(by_id[case_id]["passed"] for case_id in case_ids),
            }
        )
    return groups


def evaluate(dataset_path: Path, corpus_dir: Path, manifest_path: Path) -> dict:
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset = RetrievalEvaluationSet.model_validate(raw)
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = {item["filename"]: item for item in manifest_data["downloads"]}
    documents = []
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            for filename in source_files(dataset):
                path = corpus_dir / filename
                if not path.is_file():
                    raise FileNotFoundError(path)
                digest = verified_pdf(path, manifest)
                parsed = get_parser(str(path)).parse(str(path), batch_size=25)
                document = Document(
                    original_filename=filename,
                    storage_key=f"competition-eval-{filename}",
                    size_bytes=path.stat().st_size,
                    sha256=digest,
                    status=DocumentStatus.READY,
                    title=parsed.title,
                    page_count=len(parsed.pages),
                )
                session.add(document)
                session.flush()
                chunk_count = replace_document_chunks(session, document.id, parsed.to_dict())
                documents.append(
                    {
                        "filename": filename,
                        "sha256": digest,
                        "page_count": len(parsed.pages),
                        "chunk_count": chunk_count,
                        "title": parsed.title,
                    }
                )
                del parsed
            session.commit()
            retrieval = evaluate_retrieval(session, dataset, retriever=LexicalRetriever(session))
            groups = comparison_groups(raw, retrieval["cases"])
    finally:
        engine.dispose()
    status = (
        "passed"
        if retrieval["status"] == "passed" and all(group["passed"] for group in groups)
        else "failed"
    )
    return {
        "schema_version": "1.0",
        "status": status,
        "dataset_id": dataset.dataset_id,
        "documents": documents,
        "comparison_groups": groups,
        "retrieval": retrieval,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=PROJECT_ROOT / "evals/competition_real_pdfs.json"
    )
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT.parent / "docs/pdf-regression-corpus.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.dataset, args.corpus_dir, args.manifest)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "dataset_id": report["dataset_id"],
                "documents": report["documents"],
                "comparison_groups": report["comparison_groups"],
                "metrics": report["retrieval"]["metrics"],
                "failed_cases": [
                    item["case_id"] for item in report["retrieval"]["cases"] if not item["passed"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
