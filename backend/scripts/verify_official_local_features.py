"""Exercise real reading, graph and future-work APIs with isolated official PDF copies.

Does not call cloud models or write the user's running library. Graph edges are
audited independently against native PDF reference pages, not the matching score.
"""

import argparse
import json
import os
import re
import shutil
import unicodedata
from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from official_functional_inputs import PROJECT, digest, isolated_output, load_inputs, write_json

GRAPH_FILES = [
    "1607.06450v1.pdf",
    "1706.03762v1.pdf",
    "1707.06347v2.pdf",
    "1910.02054v3.pdf",
    "1910.10683v4.pdf",
    "2001.08361v1.pdf",
    "2002.04745v2.pdf",
    "2005.14165v4.pdf",
    "2010.11929v2.pdf",
    "2101.03961v3.pdf",
    "2103.00020v1.pdf",
    "2106.09685v2.pdf",
    "2107.03374v2.pdf",
    "2201.11903v6.pdf",
    "2203.02155v1.pdf",
    "2205.14135v2.pdf",
    "2305.14314v1.pdf",
    "2305.18290v3.pdf",
    "N19-1423.pdf",
    "language_understanding_paper.pdf",
]


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", text).casefold())


def flatten(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from flatten(node.get("children", []))


def configure(root: Path) -> None:
    for name, value in {
        "DATABASE_URL": f"sqlite:///{(root / 'acceptance.db').as_posix()}",
        "DATA_DIR": str(root),
        "UPLOAD_DIR": str(root / "uploads"),
        "PARSED_DIR": str(root / "parsed"),
        "TRANSLATION_DIR": str(root / "translations"),
        "VECTOR_SEARCH_ENABLED": "false",
        "LLM_PROVIDER": "mock",
        "LLM_API_KEY": "",
        "FRONTEND_ORIGIN": "http://localhost:3300",
    }.items():
        os.environ[name] = value


def seed(root: Path, inputs: list[dict]) -> dict[str, dict]:
    from app.core.database import SessionLocal, init_db
    from app.models.document import Document, DocumentStatus

    init_db()
    root.joinpath("uploads").mkdir(exist_ok=True)
    root.joinpath("parsed").mkdir(exist_ok=True)
    indexed = {}
    with SessionLocal() as db:
        for item in inputs:
            row = item["row"]
            document_id = str(uuid5(NAMESPACE_URL, "official-acceptance:" + row["source_sha256"]))
            name = row["filename"]
            storage_key = document_id + ".pdf"
            shutil.copyfile(item["source"], root / "uploads" / storage_key)
            shutil.copyfile(item["ast"], root / "parsed" / (document_id + ".json"))
            identifier = re.match(r"(\d{4}\.\d{4,5})(?:v(\d+))?", name)
            db.add(
                Document(
                    id=document_id,
                    original_filename=name,
                    storage_key=storage_key,
                    size_bytes=row["size_bytes"],
                    sha256=row["source_sha256"],
                    status=DocumentStatus.READY,
                    title=row["title"],
                    authors_json=json.dumps(row["author_names"], ensure_ascii=False),
                    page_count=row["page_count"],
                    arxiv_id=identifier[1] if identifier else None,
                    arxiv_version=int(identifier[2]) if identifier and identifier[2] else None,
                    parser_name=row["parser"],
                    parser_version=row.get("parser_version", "layout98"),
                )
            )
            indexed[document_id] = item
            item["document_id"] = document_id
        db.commit()
    write_json(
        root / "fixture.json",
        [
            {
                "document_id": i,
                "filename": x["row"]["filename"],
                "source_sha256": x["row"]["source_sha256"],
                "ast_sha256": x["ast_sha256"],
            }
            for i, x in indexed.items()
        ],
    )
    return indexed


def verify_reader(client, item: dict) -> dict:
    prefix = "/api/v1/documents/" + item["document_id"]
    errors = []
    parsed = item["parsed"]
    blocks = {(pg["page_number"], b["block_id"]): b for pg in parsed["pages"] for b in pg["blocks"]}
    dimensions = {pg["page_number"]: (pg["width"], pg["height"]) for pg in parsed["pages"]}

    def anchor(page, ids, bbox, label):
        if page not in dimensions or not ids or any((page, i) not in blocks for i in ids):
            errors.append(f"{label}: invalid page/block anchor")
        if bbox is None:
            errors.append(f"{label}: missing bbox")
        elif len(bbox) != 4 or page not in dimensions:
            errors.append(f"{label}: invalid bbox")
        else:
            w, h = dimensions[page]
            x0, y0, x1, y1 = bbox
            if not (-2 <= x0 <= x1 <= w + 2 and -2 <= y0 <= y1 <= h + 2):
                errors.append(f"{label}: bbox outside page")

    content = client.get(prefix + "/content")
    if content.status_code != 200 or content.json() != parsed:
        errors.append("content API does not return exact persisted AST")
    outline_response = client.get(prefix + "/outline")
    references_response = client.get(prefix + "/references")
    outline = list(flatten(outline_response.json().get("items", [])))
    refs = references_response.json().get("items", [])
    mentions = references_response.json().get("mentions", [])
    if outline_response.status_code != 200 or references_response.status_code != 200:
        errors.append("outline/reference API error")
    for node in outline:
        anchor(node["page_number"], [node["block_id"]], node.get("bbox"), "outline")
    labels = {str(r["label"]): r for r in refs}
    for reference in refs:
        # A bibliography entry can continue onto the following page. Its primary
        # navigation anchor must be on the declared page; remaining blocks may
        # span pages but must exist in the actual AST, never be silently discarded.
        ids = reference["block_ids"]
        anchor(reference["page_number"], ids[:1], reference.get("bbox"), "reference")
        all_ids = {block_id for _, block_id in blocks}
        if any(block_id not in all_ids for block_id in ids):
            errors.append("reference continuation block is missing")
    for mention in mentions:
        anchor(mention["page_number"], [mention["block_id"]], mention.get("bbox"), "mention")
        if mention["label"] not in labels:
            errors.append("citation has no return target")
    if len({m["citation_id"] for m in mentions}) != len(mentions):
        errors.append("nonunique citation navigation identities")
    file_response = client.get(prefix + "/file", headers={"Range": "bytes=0-63"})
    if (
        file_response.status_code != 206
        or file_response.content != item["source"].read_bytes()[:64]
    ):
        errors.append("PDF range-serving failed")
    return {
        "filename": item["row"]["filename"],
        "status": "failed" if errors else "passed",
        "source_sha256": item["row"]["source_sha256"],
        "ast_sha256": item["ast_sha256"],
        "pages": len(parsed["pages"]),
        "outline_anchors": len(outline),
        "reference_anchors": len(refs),
        "mention_anchors": len(mentions),
        "backlink_labels": len({m["label"] for m in mentions}),
        "cross_page_reference_entries": sum(
            any((r["page_number"], b) not in blocks for b in r["block_ids"]) for r in refs
        ),
        "author_year_references": sum(str(r["label"]).startswith("author-year-") for r in refs),
        "failures": errors,
    }


def verify_graph(client, selected: list[dict], indexed: dict[str, dict], root: Path) -> dict:
    import pymupdf

    response = client.post(
        "/api/v1/citation-graph", json={"document_ids": [x["document_id"] for x in selected]}
    )
    if response.status_code != 200:
        return {"status": "failed", "http_status": response.status_code, "error": response.text}
    graph = response.json()
    write_json(root / "graph-20.json", graph)
    errors, audit = [], []
    outgoing, incoming = Counter(), Counter()
    for edge in graph["edges"]:
        source = indexed[edge["source_document_id"]]
        target = indexed[edge["target_document_id"]]
        outgoing[edge["source_document_id"]] += 1
        incoming[edge["target_document_id"]] += 1
        refs = {r["reference_id"]: r for r in source["parsed"]["references"]}
        proofs = []
        with pymupdf.open(source["source"]) as pdf:
            for ref_id in edge["reference_ids"]:
                reference = refs.get(ref_id)
                if not reference:
                    errors.append("edge refers to missing source reference")
                    continue
                text = reference["text"]
                native = pdf[reference["page_number"] - 1].get_text()
                exact_title = compact(target["row"]["title"]) in compact(text)
                arxiv_id = re.match(r"\d{4}\.\d{4,5}", target["row"]["filename"])
                exact_id = bool(arxiv_id and arxiv_id[0] in text)
                native_found = compact(text) in compact(native)
                proofs.append(
                    {
                        "reference_id": ref_id,
                        "page_number": reference["page_number"],
                        "exact_title_or_id": exact_title or exact_id,
                        "native_page_contains_reference": native_found,
                    }
                )
        supported = bool(proofs) and all(
            p["exact_title_or_id"] and p["native_page_contains_reference"] for p in proofs
        )
        audit.append(
            {
                "source": source["row"]["filename"],
                "target": target["row"]["filename"],
                "status": "source_verified" if supported else "needs_review",
                "proofs": proofs,
            }
        )
    for node in graph["nodes"]:
        if (
            node["out_degree"] != outgoing[node["document_id"]]
            or node["in_degree"] != incoming[node["document_id"]]
        ):
            errors.append("degree mismatch")
    if len(graph["nodes"]) != 20 or len({compact(n["title"]) for n in graph["nodes"]}) != 20:
        errors.append("not 20 distinct paper identities")
    if abs(sum(n["pagerank"] for n in graph["nodes"]) - 1) > 0.00003:
        errors.append("PageRank not normalized")
    uncertain = sum(x["status"] == "needs_review" for x in audit)
    return {
        "status": "failed" if errors else "needs_review" if uncertain else "passed",
        "stats": graph["stats"],
        "edge_audit": audit,
        "unresolved_edges": uncertain,
        "failures": errors,
        "scope": (
            "Native-page exact-title/ID edge support and local graph arithmetic; "
            "roles are local heuristics"
        ),
    }


def verify_review(client, selected: list[dict], indexed: dict[str, dict], root: Path) -> dict:
    request = {
        "document_ids": [x["document_id"] for x in selected],
        "max_evidence": 60,
        "generation_mode": "extractive",
    }
    response = client.post("/api/v1/reviews/generate", json=request)
    if response.status_code != 200:
        return {"status": "failed", "http_status": response.status_code, "error": response.text}
    review = response.json()
    write_json(root / "review-20.json", review)
    errors = []
    generation_evidence = review["evidence"]
    evidence = {
        e["evidence_id"]: e
        for e in [*generation_evidence, *review.get("future_work_evidence", [])]
    }
    for e in evidence.values():
        page = indexed[e["document_id"]]["parsed"]["pages"][e["page_number"] - 1]
        blocks = {b["block_id"]: b for b in page["blocks"]}
        source = " ".join(
            " ".join(blocks[b]["text"].split()) for b in e["block_ids"] if b in blocks
        )
        if not e["block_ids"]:
            source = " ".join(indexed[e["document_id"]]["parsed"].get("abstract", "").split())
        if " ".join(e["quote"].split()) not in source:
            errors.append("review evidence anchor/quote mismatch")
    coverage = {e["document_id"] for e in generation_evidence}
    missing = [x["row"]["filename"] for x in selected if x["document_id"] not in coverage]
    if missing:
        errors.append("selected papers lost to evidence budget")
    decision_checks = []
    for direction in review["future_directions"]:
        e = evidence[direction["source_evidence_id"]]
        if not e["block_ids"]:
            continue
        source = {k: e[k] for k in ("document_id", "page_number", "block_ids", "quote")}
        path = "/api/v1/reviews/future-directions/" + direction["direction_id"] + "/decision"
        negative = client.put(
            path,
            json={
                "source": source,
                "decision": "confirmed_addressed",
                "reason": "验收测试不能在缺少后续原文证据时宣称已解决。",
                "progress": [],
            },
        )
        valid = client.put(
            path,
            json={
                "source": source,
                "decision": "needs_review",
                "reason": "真实测试段落已核对，后续科学结论仍待人工审查。",
                "progress": [],
            },
        )
        decision_checks.append(
            {
                "direction_id": direction["direction_id"],
                "unproven_confirmation_status": negative.status_code,
                "needs_review_save_status": valid.status_code,
            }
        )
        if negative.status_code != 422 or valid.status_code != 200:
            errors.append("future decision guard/save failed")
    refreshed = client.post("/api/v1/reviews/generate", json=request).json()
    confirmed_ids = {
        d["direction_id"]
        for d in refreshed.get("future_directions", [])
        if (d.get("confirmation") or {}).get("decision") == "needs_review"
    }
    if any(c["direction_id"] not in confirmed_ids for c in decision_checks):
        errors.append("future decision did not persist")
    return {
        "status": "failed" if errors else "passed",
        "selected_papers": 20,
        "covered_papers": len(coverage),
        "evidence_count": len(evidence),
        "generation_evidence_count": len(generation_evidence),
        "local_future_evidence_count": len(review.get("future_work_evidence", [])),
        "missing_papers": missing,
        "future_directions": len(review["future_directions"]),
        "decision_checks": decision_checks,
        "failures": errors,
        "scope": (
            "Extractive source-bound review and guarded human decisions; "
            "not generated review or solved-idea proof"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=PROJECT / "科研小助手/pdf")
    parser.add_argument("--parse-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    inputs = load_inputs(args.corpus, args.parse_report)
    root = isolated_output(args.output_dir)
    configure(root)
    from fastapi.testclient import TestClient

    from app.main import app

    indexed = seed(root, inputs)
    by_name = {x["row"]["filename"]: x for x in inputs}
    selected = [by_name[name] for name in GRAPH_FILES]
    report = {
        "status": "running",
        "cloud_calls": 0,
        "reader": [],
        "scope": "isolated real API acceptance",
    }
    with TestClient(app) as client:
        for item in inputs:
            row = verify_reader(client, item)
            report["reader"].append(row)
            write_json(root / "report.json", report)
            print(
                f"reader {row['filename']}: {row['status']} ({row['mention_anchors']} mentions)",
                flush=True,
            )
        report["graph"] = verify_graph(client, selected, indexed, root)
        print(f"graph 20: {report['graph']['status']}", flush=True)
        report["review"] = verify_review(client, selected, indexed, root)
        print(
            f"review 20: {report['review']['status']} "
            f"({report['review'].get('covered_papers')} covered)",
            flush=True,
        )
    report["source_unchanged"] = all(
        digest(x["source"]) == x["row"]["source_sha256"] for x in inputs
    )
    report["summary"] = {
        "reader_passed": sum(r["status"] == "passed" for r in report["reader"]),
        "pages": sum(r["pages"] for r in report["reader"]),
        "mentions": sum(r["mention_anchors"] for r in report["reader"]),
    }
    report["status"] = (
        "passed"
        if report["summary"]["reader_passed"] == 39
        and report["graph"]["status"] == "passed"
        and report["review"]["status"] == "passed"
        and report["source_unchanged"]
        else "needs_repair_or_review"
    )
    write_json(root / "report.json", report)
    print(json.dumps(report["summary"]), flush=True)
    if args.serve:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
