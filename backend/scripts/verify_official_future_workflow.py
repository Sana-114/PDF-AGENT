"""Real-source future-work negative controls and durable review API in isolated storage.

No cloud requests and no claim that semantic overlap proves an idea resolved.
The independent targets below refer to public official passages, not prompts.
"""

import argparse
import asyncio
import json
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_local_features import GRAPH_FILES, configure, seed


def run(args):
    root = isolated_output(args.output_dir)
    inputs = saved_inputs(args.fixture, GRAPH_FILES)
    configure(root)
    from fastapi.testclient import TestClient

    from app.core.database import SessionLocal
    from app.llm.extractive import ExtractiveProvider
    from app.main import app
    from app.models.document import Document
    from app.services.evidence_review import EvidenceReviewService

    indexed = seed(root, inputs)
    names = {item["row"]["filename"]: key for key, item in indexed.items()}
    with SessionLocal() as db:
        result = asyncio.run(
            EvidenceReviewService(db, provider=ExtractiveProvider()).generate(
                [db.get(Document, key) for key in indexed], max_evidence=40
            )
        )
    write_json(root / "review-before.json", result.model_dump(mode="json"))
    directions = result.future_directions
    ln = [d for d in directions if d.source_document_id == names["2002.04745v2.pdf"]]
    lora = [d for d in directions if d.source_document_id == names["2106.09685v2.pdf"]]
    qlora = [d for d in directions if d.source_document_id == names["2305.14314v1.pdf"]]
    transformer = next(
        d
        for d in directions
        if d.source_document_id == names["1706.03762v1.pdf"]
        and "modalities other than text" in d.text
    )
    checks = {
        "mixed_conclusion_selects_future_sentence": any(
            "In the future, we will investigate other strategies" in d.text for d in ln
        ),
        "running_title_not_direction": not any(d.text == d.source_title for d in directions),
        "lora_future_layers_not_completed_summary": any(
            "We leave the empirical investigation" in d.text and "MLP layers" in d.text
            for d in lora
        ),
        "lora_current_nlg_experiment_excluded": not any(
            "we hope to answer" in d.text for d in lora
        ),
        "qlora_release_not_open_problem": not any(
            "We release all model generations" in d.text for d in qlora
        ),
        "qlora_paging_question_preserved": any("paging process" in d.text for d in qlora),
        "transformer_multimodal_plan_preserved": bool(transformer),
        "no_automatic_resolved_claim": not any(
            d.status == "confirmed_addressed" for d in directions
        ),
    }
    anchors = {e.evidence_id: e for e in result.future_work_evidence}
    source = anchors[transformer.source_evidence_id]
    progress = next(
        e
        for e in result.evidence
        if e.document_id == names["2010.11929v2.pdf"] and "image patches" in e.quote
    )

    def passage(e):
        return {k: getattr(e, k) for k in ("document_id", "page_number", "block_ids", "quote")}

    base = {
        "source": passage(source),
        "decision": "needs_review",
        "reason": "ViT provides image-classification progress only; "
        "this does not resolve audio, video or all local-attention goals.",
        "progress": [passage(progress)],
    }
    route = "/api/v1/reviews/future-directions/" + transformer.direction_id + "/decision"
    responses = []
    with TestClient(app) as client:
        for label, body, expected in (
            ("valid_partial_progress_review", base, 200),
            (
                "resolved_without_progress",
                {**base, "decision": "confirmed_addressed", "progress": []},
                422,
            ),
            (
                "resolved_same_paper",
                {**base, "decision": "confirmed_addressed", "progress": [passage(source)]},
                422,
            ),
            (
                "source_quote_mismatch",
                {
                    **base,
                    "source": {
                        **passage(source),
                        "quote": "This sentence is not in the cited paper.",
                    },
                },
                422,
            ),
            (
                "source_page_mismatch",
                {**base, "source": {**passage(source), "page_number": 999}},
                422,
            ),
        ):
            response = client.put(route, json=body)
            responses.append(
                {
                    "case": label,
                    "request": body,
                    "status_code": response.status_code,
                    "response": response.json(),
                }
            )
            checks[label] = response.status_code == expected
        regenerated = client.post(
            "/api/v1/reviews/generate",
            json={
                "document_ids": list(indexed),
                "generation_mode": "extractive",
                "max_evidence": 40,
            },
        )
        write_json(root / "review-after.json", regenerated.json())
        saved = next(
            (
                d
                for d in regenerated.json().get("future_directions", [])
                if d["direction_id"] == transformer.direction_id
            ),
            {},
        )
        checks["human_review_persists_not_resolved"] = (
            regenerated.status_code == 200
            and saved.get("confirmation", {}).get("decision") == "needs_review"
            and saved.get("status") != "confirmed_addressed"
        )
    report = {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "cloud_calls": 0,
        "production_library_modified": False,
        "scientific_auto_resolution": False,
        "scope": "Eight fixed official-source controls and six actual review API checks; "
        "not solved-idea classification",
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
    }
    write_json(root / "api-responses.json", responses)
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
