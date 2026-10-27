"""Replay saved scientific outputs through current publication rules, with zero cloud calls."""

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from verify_official_output_features import configure, select_sources


async def run(args):
    args.previous = args.previous.resolve()
    if args.previous == TEMP_ROOT or not args.previous.is_relative_to(TEMP_ROOT):
        raise ValueError("Only saved isolated acceptance outputs can be replayed")
    root = isolated_output(args.output_dir)
    configure(root)
    previous = json.loads((args.previous / "report.json").read_bytes())
    names = [row["filename"] for row in previous["sources"]]
    rows = select_sources(args.fixture, names)
    if rows != previous["sources"]:
        raise ValueError("Saved output source/AST scope changed")
    output_name = "writing-outline.json" if args.mode == "writing" else "generated-review.json"
    old_path = args.previous / output_name

    from inspect_runtime_source import inspect_source

    from app.core.database import SessionLocal, init_db
    from app.llm.base import GeneratedAnswer, GeneratedClaim
    from app.models.document import Document, DocumentStatus
    from app.schemas.agent import AskResponse, EvidenceAnchor
    from app.schemas.review import ResearchReviewRead
    from app.schemas.writing import WritingOutlineRead
    from app.services.evidence_review import EvidenceReviewService
    from app.services.grounded_text import INLINE_ANCHOR, audit_grounded_claims
    from app.services.writing_outline import WritingOutlineService

    old = (
        WritingOutlineRead if args.mode == "writing" else ResearchReviewRead
    ).model_validate_json(old_path.read_bytes())
    evidence = [EvidenceAnchor.model_validate(e.model_dump()) for e in old.evidence]
    saved_claims = [*old.claims, *getattr(old, "deferred_claims", [])]
    raw_claims = None
    raw_answer = None
    if getattr(args, "raw_generated", False):
        from app.llm.openai_responses import OpenAIResponsesProvider
        from app.llm.thematic_review import render_thematic_review

        calls = json.loads((args.previous / "calls.json").read_bytes())
        if len(calls) != 1 or calls[0].get("status") != "returned":
            raise ValueError("Expected one preserved successful generation call")
        payload = json.loads(OpenAIResponsesProvider._extract_output_text(calls[0]["response"]))
        if args.mode == "review":
            # Keep the same root, topic, role, length and citation contract as
            # production. Replaying rejected original rows must not bypass it.
            raw_answer = render_thematic_review(payload, evidence)
            raw_claims = raw_answer.claims
        else:
            raw_claims = [GeneratedClaim(item["text"], item["evidence_ids"])
                          for item in payload["claims"]]

    class SavedAgent:
        async def ask(self, request, **kwargs):
            if raw_claims is not None:
                accepted, audit = audit_grounded_claims(raw_claims, evidence)
            else:
                accepted, audit = saved_claims, getattr(old, "grounding_audit", None)
            return AskResponse(
                answer="",
                claims=accepted,
                evidence=evidence,
                provider="saved-output",
                model=old.model,
                insufficient_evidence=old.insufficient_evidence,
                grounding_audit=audit,
            )

    class SavedProvider:
        name, model = "saved-output", old.model

        async def generate_grounded_answer(self, question, rebuilt):
            if [e.model_dump() for e in rebuilt] != [e.model_dump() for e in evidence]:
                raise ValueError("Replay must not silently change the saved generation evidence")
            if raw_answer is not None:
                return raw_answer
            return GeneratedAnswer(
                answer="",
                claims=[
                    GeneratedClaim(
                        c.text,
                        c.evidence_ids,
                        next((t.title for t in old.themes if i in t.claim_indices), None),
                        "cross_paper"
                        if any(i in t.cross_paper_claim_indices for t in old.themes)
                        else "single_paper",
                    )
                    for i, c in enumerate(old.claims)
                ],
            )

    init_db()
    with SessionLocal() as db:
        docs = []
        for row in rows:
            doc_id = row["document_id"]
            ast = args.fixture / "parsed" / (doc_id + ".json")
            parsed = json.loads(ast.read_bytes())
            (root / "parsed").mkdir(exist_ok=True)
            shutil.copyfile(ast, root / "parsed" / ast.name)
            doc = Document(
                id=doc_id,
                original_filename=row["filename"],
                storage_key=doc_id + ".pdf",
                sha256=row["source_sha256"],
                size_bytes=1,
                status=DocumentStatus.READY,
                title=parsed["title"],
                authors_json=json.dumps(parsed.get("authors", [])),
                page_count=len(parsed["pages"]),
                arxiv_id=row["filename"].split("v")[0],
            )
            db.add(doc)
            docs.append(doc)
        db.commit()
        if args.mode == "writing":
            result = await WritingOutlineService(db, agent=SavedAgent()).generate(
                old.idea, docs, language="en"
            )
        else:
            result = await EvidenceReviewService(db, provider=SavedProvider()).generate(
                docs, max_evidence=previous["evidence_budget"]
            )
    write_json(root / output_name, result.model_dump(mode="json"))
    old_texts = [INLINE_ANCHOR.sub("", c.text).strip()
                 for c in (raw_claims if raw_claims is not None else saved_claims)]
    kept = {c.text for c in result.claims}
    deferred = {c.text for c in getattr(result, "deferred_claims", [])}
    report = {
        "evaluation_kind": "saved_output_publication_replay_not_new_generation",
        "mode": args.mode,
        "model_calls": 0,
        "production_library_modified": False,
        "sources": rows,
        "original_report_sha256": digest(args.previous / "report.json"),
        "original_output_sha256": digest(old_path),
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "original_claims": len(old.claims),
        "replayed_input_claims": len(old_texts),
        "replayed_raw_generated": raw_claims is not None,
        "raw_calls_sha256": digest(args.previous / "calls.json")
        if raw_claims is not None else None,
        "replayed_structural_field_rejections": raw_answer.field_rejections
        if raw_answer is not None else {},
        "published_claims": len(result.claims),
        "deferred_original_indices": [i for i, text in enumerate(old_texts) if text in deferred],
        "withheld_original_indices": [i for i, text in enumerate(old_texts)
                                      if text not in kept | deferred],
        "warnings": result.warnings,
        "semantic_review": "pending_original_page_inspection",
    }
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "mode",
                    "model_calls",
                    "original_claims",
                    "published_claims",
                    "withheld_original_indices",
                )
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["writing", "review"], required=True)
    parser.add_argument("--raw-generated", action="store_true",
                        help="Audit preserved raw generation with original evidence and schema")
    for name in ("fixture", "previous", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
