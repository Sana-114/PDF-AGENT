"""Verify a SHA-pinned real PDF author query without any LLM or vector service calls."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.parsers.registry import get_parser  # noqa: E402
from app.services.author_evidence import AUTHOR_SECTION  # noqa: E402
from app.services.chunking import replace_document_chunks  # noqa: E402
from app.services.retrieval import LexicalRetriever  # noqa: E402


def evaluate(args) -> dict:
    if args.output.exists():
        raise ValueError("Reports cannot be overwritten")
    digest = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    if digest != args.expected_sha:
        raise ValueError("Input PDF SHA mismatch")
    parsed = get_parser(str(args.pdf)).parse(str(args.pdf), batch_size=25)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        doc = Document(original_filename=args.pdf.name, storage_key="isolated.pdf", size_bytes=1,
                       sha256=digest, status=DocumentStatus.READY, title=parsed.title)
        session.add(doc)
        session.flush()
        count = replace_document_chunks(session, doc.id, parsed.to_dict())
        session.commit()
        evidence = LexicalRetriever(session).search(
            f"第 {args.ordinal} 作者是谁，在哪个公司或机构工作？", [doc.id], 6
        )
        metadata = [item for item in evidence if item.section == AUTHOR_SECTION]
        passed = bool(metadata and args.ordinal <= len(parsed.authors)
                      and parsed.authors[args.ordinal - 1] == args.expected_author
                      and f"{args.ordinal}. {args.expected_author}" in metadata[0].quote
                      and args.expected_affiliation in metadata[0].quote
                      and metadata[0].bbox and metadata[0].block_ids)
        report = {
            "status": "passed" if passed else "failed", "llm_calls": 0,
            "retriever": "lexical", "production_library_modified": False,
            "source_sha256": digest, "filename": args.pdf.name,
            "author_order": parsed.authors, "chunks": count,
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
    engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--ordinal", type=int, required=True)
    parser.add_argument("--expected-author", required=True)
    parser.add_argument("--expected-affiliation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.ordinal < 1:
        parser.error("ordinal must be positive")
    result = evaluate(args)
    print(json.dumps({key: result[key] for key in ("status", "llm_calls", "chunks")}))
    if result["status"] != "passed":
        raise SystemExit(1)
