"""Publish only acceptance metrics and hashes, never cloud requests or paper excerpts."""

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree

from official_functional_inputs import PROJECT, TEMP_ROOT, digest, write_json


def summarize(root: Path) -> dict:
    root = root.resolve()
    if not root.is_relative_to(TEMP_ROOT) or root == TEMP_ROOT:
        raise ValueError("Requires isolated acceptance reports")
    sources = {}

    def read(relative):
        path = root / relative
        sources[relative] = digest(path)
        return json.loads(path.read_text(encoding="utf-8"))

    local = read("local-features-final/report.json")
    translation = read("full-translation-1/report.json")
    export = read("local-features-final/translation-export-api-003.json")
    browser = read("browser-reader-final-direct/report.json")
    generated = read("generated-review-20-1/report.json")
    pending_first = read("pending-two-1/report.json")
    pending_final = read("pending-final-1/report.json")
    attempts = [
        read(str(p.relative_to(root)).replace("\\", "/"))
        for p in sorted((root / "full-translation-1/attempts").glob("*.json"))
    ]
    unit_path = root / "full-unit-functional-final.xml"
    unit = ElementTree.parse(unit_path).getroot()[0].attrib
    sources[unit_path.name] = digest(unit_path)
    if (
        local["status"] != "passed"
        or translation["status"] != "passed"
        or export["status"] != "passed"
        or browser["status"] != "passed"
    ):
        raise ValueError("Do not publish a passing functional summary from failed reports")
    counts = {
        key: sum(row[key] for row in local["reader"])
        for key in (
            "outline_anchors",
            "reference_anchors",
            "mention_anchors",
            "cross_page_reference_entries",
            "author_year_references",
        )
    }
    cloud_counts = {
        "pending_qa_before_repair": pending_first["model_calls"],
        "pending_qa_after_repair": pending_final["model_calls"],
        "whole_translation_all_attempts": sum(x["cloud_calls_this_attempt"] for x in attempts),
        "twenty_paper_review": generated["cloud_calls"],
    }
    code_files = [
        "backend/app/services/academic_translation.py",
        "backend/app/services/document_translation.py",
        "backend/app/services/document_content.py",
        "backend/app/services/translation_store.py",
        "backend/app/services/evidence_review.py",
        "backend/app/api/routes/documents.py",
        "backend/app/llm/model_fields.py",
        "frontend/e2e/officialReaderAcceptance.mjs",
    ]
    return {
        "date": "2026-10-12",
        "acceptance_status": "partial_not_competition_release",
        "scope": "Official corpus reading APIs, 20-paper graph, whole Transformer v1 translation "
        "and real browser; not a frozen all-module production candidate",
        "cloud_calls": cloud_counts,
        "cloud_calls_total_this_work": sum(cloud_counts.values()),
        "automatic_cloud_retries": 0,
        "production_library_modified": False,
        "git_commit_or_push_performed": False,
        "reader": {
            **local["summary"],
            **counts,
            "documents_with_bracket_backlinks": sum(
                x["mention_anchors"] > 0 for x in local["reader"]
            ),
            "all_citation_styles_accepted": False,
            "files": local["reader"],
        },
        "graph": local["graph"],
        "extractive_review_and_future_decisions": local["review"],
        "generated_review": {
            k: generated[k]
            for k in (
                "status",
                "evidence_papers",
                "evidence_count",
                "published_claims",
                "provider",
                "model",
                "warnings",
            )
        },
        "generated_review_quality_status": "needs_concise_synthesis_and_graph_relation_audit",
        "translation": {
            "status": translation["status"],
            "source": translation["identity"],
            "pages": translation["pages"],
            "total_aligned_blocks": sum(p["actual_blocks"] for p in translation["pages"]),
            "all_attempts": [
                {
                    "status": a["status"],
                    "calls": a["cloud_calls_this_attempt"],
                    "error": a.get("error"),
                }
                for a in attempts
            ],
            "manual_sample_pages": [1, 2, 6, 8, 12],
            "manual_samples_exhaustive": False,
            "exports": [
                {"format": e["format"], "mode": e["mode"], "sha256": e["sha256"]}
                for e in translation["exports"]
            ],
            "export_api": export,
        },
        "browser": browser,
        "regression": {
            "backend_tests": int(unit["tests"]),
            "backend_failures": int(unit["failures"]),
            "backend_errors": int(unit["errors"]),
            "frontend_tests": 29,
            "frontend_typecheck": "passed",
            "ruff": "passed",
        },
        "report_sha256": sources,
        "final_worktree_code_sha256": {p: digest(PROJECT / p) for p in code_files},
        "remaining": [
            "Generated 20-paper review is lengthy and not yet a verified "
            "concise graph-based synthesis",
            "Author-year citation automatic bidirectional linking is not implemented",
            "Live scholarly search/download, subscription updates and "
            "recommendation interactions need current acceptance",
            "Writing bibliographies, CSV/chart/caption acceptance need a current broader test",
            "Scan Adam guard, Pre-LN answer evaluator and GPT-1 response "
            "completeness retain failed QA records",
            "Cross-paper QA is not a stable frozen-candidate pass; paused targeted GPT repetitions",
            "Final Docker/worker runtime acceptance, production frontend, "
            "public deployment and submission artifacts remain",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.root)
    target = PROJECT / "docs/evidence/official-functional-acceptance-2026-10-12.json"
    if target.exists():
        raise ValueError("Preserve published snapshots; choose a new dated report")
    write_json(target, report)
    print(
        json.dumps(
            {
                "output": str(target),
                "cloud_calls": report["cloud_calls_total_this_work"],
                "regression": report["regression"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
