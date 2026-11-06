"""Grade duplicate decisions and citation use against labelled official sources offline."""

import argparse
import asyncio
import json
import unicodedata
from itertools import combinations
from pathlib import Path

import fitz
from official_functional_inputs import digest, isolated_output, write_json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from verify_official_output_features import select_sources

from app.api.routes.recommendations import list_recommendations, set_recommendation_feedback
from app.core.config import Settings
from app.core.database import Base
from app.models.recommendation import ArxivSubscription
from app.schemas.discovery import PaperCandidate
from app.schemas.recommendation import RecommendationFeedbackRequest
from app.services.citation_value import citation_values
from app.services.document_content import reference_links
from app.services.document_duplicates import assess_duplicate
from app.services.fingerprints import bottom_k_signature, extract_document_arxiv_identity
from app.services.recommendations import RecommendationService

# Same paper by title/authors in the source inventory; different bytes and layouts count.
SAME_WORKS = (
    {"1706.03762v1.pdf", "1706.03762v7.pdf", "1706.03762v1_img.pdf"},
    {"209_Emergent_Abilities_of_Larg.pdf", "2206.07682v2.pdf"},
)

# Physical page, bibliography label, local phrase, expected role of this occurrence.
CITATIONS = {
    "1706.03762v1.pdf": (
        (7, "18", "We used the Adam optimizer", "method"),
        (7, "30", "We apply dropout", "method"),
        (7, "32", "we employed label smoothing", "method"),
    ),
    "1607.06450v1.pdf": (
        (1, "author-year-1", "computer vision", "background"),
        (1, "author-year-2", "speech processing", "background"),
    ),
    "2001.08361v1.pdf": (
        (12, "SLA+18", "see also", "incidental"),
        (12, "ZLN+19", "see also", "incidental"),
        (12, "MKAT18", "was developed in", "background"),
    ),
}

# Additional pages selected after the rule change. Report separately from repair cases.
NEW_PAGES = {
    "2010.11929v2.pdf": (
        (3, "author-year-47", "we follow the original Transformer", "method"),
        (5, "author-year-16", "For the baseline CNNs, we use ResNet", "baseline"),
    ),
    "2205.14135v2.pdf": (
        (7, "17", "we use the fixed butterfly sparsity pattern", "method"),
        (7, "16", "shown to be able to approximate arbitrary sparsity", "background"),
    ),
}


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def check_roles(filename, pdf, parsed, expectations):
    references, mentions = reference_links(parsed)
    values = citation_values(parsed)
    cases = []
    with fitz.open(pdf) as native:
        for page, label, phrase, expected in expectations:
            ref = next(item for item in references if item["label"] == label)
            if normalize(phrase) not in normalize(native[page - 1].get_text()):
                raise ValueError(f"Source phrase absent: {filename} p.{page} {phrase}")
            ids = {m["citation_id"] for m in mentions if m["label"] == label
                   and m["page_number"] == page}
            contexts = [item for item in values[ref["reference_id"]].contexts
                        if item.citation_id in ids and normalize(phrase) in normalize(item.quote)]
            cases.append({
                "filename": filename, "page": page, "label": label, "source_phrase": phrase,
                "expected": expected, "observed": [item.role for item in contexts],
                "block_ids": [item.block_id for item in contexts],
                "passed": any(item.role == expected and item.bbox for item in contexts),
            })
    return cases


async def probe_profiles(titles):
    """Change A's feedback only. Compare B before/after with publication time held fixed."""
    candidates = [
        PaperCandidate(source="arxiv", source_id=key, arxiv_id=key, title=titles[name],
                       landing_url=f"https://arxiv.org/abs/{key}", pdf_url=f"https://arxiv.org/pdf/{key}")
        for name, key in [("2106.09685v2.pdf", "2106.09685"), ("2010.11929v2.pdf", "2010.11929")]
    ]

    class RecordedFeed:
        async def search_arxiv(self, **kwargs):
            return candidates

    service = RecommendationService(discovery=RecordedFeed(), config=Settings(
        embedding_provider="hash", github_code_lookup_limit=0,
    ))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        a, b = (ArxivSubscription(name=name, query="machine learning") for name in ("A", "B"))
        db.add_all([a, b])
        db.commit()

        async def refresh(sub):
            summary = await service.refresh(db, sub.id)
            if summary.error or summary.ranking_provider != "hash" or summary.ranking_warning:
                raise RuntimeError(f"Unexpected replay outcome: {summary}")
            return {item.arxiv_id: item for item in list_recommendations(db, sub.id, 10).items}

        first_a = await refresh(a)
        first_b = await refresh(b)
        set_recommendation_feedback(
            first_a["2106.09685"].id, RecommendationFeedbackRequest(feedback="like"), db,
        )
        second_b = await refresh(b)
        before = {key: item.relevance_score for key, item in first_b.items()}
        after = {key: item.relevance_score for key, item in second_b.items()}
        if set(before) != {"2106.09685", "2010.11929"} or before.keys() != after.keys():
            raise ValueError("Replay candidate set changed")
        return {
            "backend": "in-memory SQLite, recorded titles, hash", "b_before": before,
            "b_after": after, "b_feedback_all_neutral": all(
                item.feedback == "neutral" for item in second_b.values()
            ), "b_scores_unaffected_by_a": before == after,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = isolated_output(args.output)
    names = sorted(p.name for p in args.corpus.glob("*.pdf"))
    rows = select_sources(args.fixture, names)
    required = set.union(*SAME_WORKS, set(CITATIONS), set(NEW_PAGES))
    if len(rows) != 39 or not required.issubset(names):
        raise ValueError("Expected 39 official documents including all labelled examples")
    profiles = {}
    roles = []
    new_roles = []
    for row in rows:
        name = row["filename"]
        source = args.corpus / name
        if digest(source) != row["source_sha256"]:
            raise ValueError(f"Official PDF changed: {name}")
        ast = args.fixture / "parsed" / (row["document_id"] + ".json")
        parsed = json.loads(ast.read_text(encoding="utf-8"))
        body = "\n".join(b["text"] for p in parsed["pages"] for b in p["blocks"])
        identity, _ = extract_document_arxiv_identity("upload.pdf", body)
        profiles[name] = (parsed["title"], identity, bottom_k_signature(body))
        if name in CITATIONS:
            roles.extend(check_roles(name, source, parsed, CITATIONS[name]))
        if name in NEW_PAGES:
            new_roles.extend(check_roles(name, source, parsed, NEW_PAGES[name]))

    pairs = []
    for left, right in combinations(names, 2):
        title, identifier, signature = profiles[left]
        other_title, other_identifier, other_signature = profiles[right]
        assessment = assess_duplicate(
            current_arxiv_id=identifier, current_title=title, current_signature=signature,
            existing_arxiv_id=other_identifier, existing_title=other_title,
            existing_signature=other_signature,
        )
        expected = any({left, right} <= family for family in SAME_WORKS)
        pairs.append({"left": left, "right": right, "expected": expected, **assessment.to_dict()})
    write_json(root / "pairs.json", pairs)
    false_positives = [p for p in pairs if p["qualifies"] and not p["expected"]]
    false_negatives = [p for p in pairs if p["expected"] and not p["qualifies"]]
    profiles_probe = asyncio.run(probe_profiles({name: p[0] for name, p in profiles.items()}))
    result = {
        "inputs": rows, "pairs": len(pairs), "positives": sum(p["expected"] for p in pairs),
        "expected_duplicate_pairs": [p for p in pairs if p["expected"]],
        "false_positives": false_positives, "false_negatives": false_negatives,
        "citation_occurrences": roles, "roles_passed": sum(r["passed"] for r in roles),
        "additional_pages": new_roles, "additional_passed": sum(r["passed"] for r in new_roles),
        "subscription_feedback_probe": profiles_probe,
    }
    write_json(root / "report.json", result)
    print(f"pairs={len(pairs)}, FP={len(false_positives)}, FN={len(false_negatives)}; "
          f"citation roles={result['roles_passed']}/{len(roles)}, "
          f"additional={result['additional_passed']}/{len(new_roles)}")
    passed = (
        not false_positives and not false_negatives
        and profiles_probe["b_feedback_all_neutral"] and profiles_probe["b_scores_unaffected_by_a"]
        and all(r["passed"] for r in roles + new_roles)
    )
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
