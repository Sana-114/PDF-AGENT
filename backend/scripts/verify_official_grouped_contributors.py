"""Official BLOOM grouped-credit checks in an isolated ASGI/lexical workspace.

Only the pinned public PDF is accepted. No LLM/BGE or production writes. A saved
baseline AST is optional and its comparison is never silently claimed when absent.
"""

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

import fitz
from inspect_runtime_source import inspect_source
from official_functional_inputs import digest, isolated_output, write_json
from verify_official_output_features import configure


def main() -> int:
    project = Path(__file__).resolve().parents[2]
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--corpus-dir", type=Path, required=True)
    arguments.add_argument("--output-dir", type=Path, required=True)
    arguments.add_argument("--baseline-ast", type=Path)
    args = arguments.parse_args()
    corpus_dir = args.corpus_dir.resolve()
    source = corpus_dir / "2211.05100v4.pdf"
    if digest(source) != "8033c3e611aaa120cb7294085ebd6ee297ffae9ec02393b1c4f1593d7e6ee065":
        raise ValueError("Requires the pinned official public BLOOM v4 PDF")
    root = isolated_output(args.output_dir)
    configure(root)
    os.environ.update(
        LLM_PROVIDER="mock",
        LLM_API_KEY="",
        EMBEDDING_PROVIDER="hash",
        RERANKER_PROVIDER="none",
        VECTOR_SEARCH_ENABLED="false",
    )

    from fastapi.testclient import TestClient

    from app.core.database import SessionLocal
    from app.main import app
    from app.models.document import Document, DocumentStatus
    from app.parsers.base import Block, Page
    from app.parsers.contributors import extract_contributor_groups
    from app.parsers.layout import extract_text_blocks
    from app.parsers.pymupdf_parser import PyMuPDFParser
    from app.services.author_evidence import (
        AUTHOR_SECTION,
        CONTRIBUTOR_SECTION,
        author_evidence_nodes,
        contributor_evidence_nodes,
    )
    from app.services.chunking import replace_document_chunks
    from app.services.retrieval import LexicalRetriever

    before = (
        json.loads(args.baseline_ast.read_text(encoding="utf-8")) if args.baseline_ast else None
    )
    after = PyMuPDFParser().parse(str(source), batch_size=12).to_dict()
    write_json(root / "fresh-parsed.json", after)
    source_hash = digest(source)
    groups = {group["role"]: group for group in after["contributor_groups"]}
    major = [
        "Teven Le Scao",
        "Angela Fan",
        "Christopher Akiki",
        "Ellie Pavlick",
        "Suzana Ili´c",
        "Daniel Hesslow",
        "Roman Castagné",
        "Alexandra Sasha Luccioni",
        "Fran¸cois Yvon",
        "Matthias Gallé",
        "Jonathan Tow",
        "Alexander M. Rush",
        "Stella Biderman",
        "Albert Webson",
        "Pawan Sasanka Ammanamanchi",
        "Thomas Wang",
        "Benoˆıt Sagot",
        "Niklas Muennighoff",
        "Albert Villanova del Moral",
        "Olatunji Ruwase",
        "Rachel Bawden",
        "Stas Bekman",
        "Angelina McMillan-Major",
        "Thomas Wolf",
        "Iz Beltagy",
        "Huu Nguyen",
        "Lucile Saulnier",
        "Samson Tan",
        "Pedro Ortiz Suarez",
        "Victor Sanh",
        "Hugo Lauren¸con",
        "Yacine Jernite",
        "Julien Launay",
        "Margaret Mitchell",
        "Colin Raffel",
    ]
    roles = [
        "Major Contributors",
        "Dataset",
        "Tokenization",
        "Prompt Engineering",
        "Architecture and Objective",
        "Engineering",
        "Evaluation and Interpretability",
        "Broader Impacts",
        "Applications",
        "Organization",
    ]
    checks = {
        "73_complete_physical_pages": [p["page_number"] for p in after["pages"]]
        == list(range(1, 74)),
        "collective_not_flattened_ranks": after["authors"] == ["BigScience Workshop"],
        "ten_original_role_headings": list(groups) == roles,
        "all_35_major_members_independently_transcribed": groups[roles[0]]["members"] == major,
        "prompt_cross_page_end_complete": groups["Prompt Engineering"]["members"][-1]
        == "Hadar Tojarieh"
        and [s["page_number"] for s in groups["Prompt Engineering"]["sources"]] == [1, 2],
        "applications_cross_page_name_complete": "Carlos Mu˜noz Ferrandis"
        in groups["Applications"]["members"]
        and [s["page_number"] for s in groups["Applications"]["sources"]] == [2, 3],
        "wrapped_lowercase_name_join": "Eduardo González Ponferrada"
        in groups["Dataset"]["members"],
        "wrapped_hyphen_name_preserved": "Zheng-Xin Yong" in groups["Broader Impacts"]["members"],
        "one_collective_author_evidence": len(author_evidence_nodes(after)) == 1,
        "twelve_page_scoped_contribution_segments": len(contributor_evidence_nodes(after)) == 12,
    }
    if before is not None:
        checks["body_structures_unchanged"] = all(
            before[key] == after[key]
            for key in (
                "title",
                "pages",
                "abstract",
                "outline",
                "tables",
                "figures",
                "formulas",
                "references",
                "appendices",
            )
        )
    nodes = contributor_evidence_nodes(after)
    checks["no_abstract_or_fake_global_ranks"] = all(
        "Large language models (LLMs)" not in node["text"] and "不是全局作者顺序" in node["text"]
        for node in nodes
    )

    identifier = str(uuid.uuid4())
    with TestClient(app) as client:
        shutil.copyfile(source, root / "uploads" / (identifier + ".pdf"))
        write_json(root / "parsed" / (identifier + ".json"), after)
        with SessionLocal() as db:
            db.add(
                Document(
                    id=identifier,
                    original_filename=source.name,
                    storage_key=identifier + ".pdf",
                    title=after["title"],
                    authors_json=json.dumps(after["authors"]),
                    sha256=source_hash,
                    status=DocumentStatus.READY,
                    page_count=73,
                    size_bytes=source.stat().st_size,
                )
            )
            db.commit()
            count = replace_document_chunks(db, identifier, after)
            db.commit()
            results = {}
            for question in (
                "BLOOM 署名作者 author",
                "Prompt Engineering Hadar Tojarieh",
                "Applications Mu˜noz Ferrandis",
            ):
                evidence = LexicalRetriever(db).search(question, [identifier], 6)
                results[question] = [item.model_dump(mode="json") for item in evidence]
            write_json(root / "retrieval.json", results)
        content = client.get(f"/api/v1/documents/{identifier}/content")
        write_json(root / "api-content.json", content.json())
        checks["api_preserves_new_structures"] = (
            content.status_code == 200
            and content.json()["authors"] == ["BigScience Workshop"]
            and len(content.json()["contributor_groups"]) == 10
        )
        ranged = client.get(f"/api/v1/documents/{identifier}/file", headers={"Range": "bytes=0-63"})
        checks["actual_pdf_range"] = (
            ranged.status_code == 206
            and len(ranged.content) == 64
            and ranged.content.startswith(b"%PDF-")
        )
        checks["lexical_collective_source_first"] = (
            results["BLOOM 署名作者 author"][0]["section"] == AUTHOR_SECTION
            and "1. BigScience Workshop" in results["BLOOM 署名作者 author"][0]["quote"]
        )
        checks["lexical_prompt_continuation_source"] = any(
            item["page_number"] == 2
            and item["section"] == CONTRIBUTOR_SECTION + " · Prompt Engineering"
            for item in results["Prompt Engineering Hadar Tojarieh"]
        )
        checks["lexical_application_continuation_source"] = any(
            item["page_number"] == 3 and item["section"] == CONTRIBUTOR_SECTION + " · Applications"
            for item in results["Applications Mu˜noz Ferrandis"]
        )

    corpus = []
    files = sorted(corpus_dir.glob("*.pdf"))
    assert len(files) == 39
    for path in files:
        raw_pages, pages = [], []
        with fitz.open(path) as native:
            for index in range(min(3, len(native))):
                raw = extract_text_blocks(native[index].get_text("dict", sort=False), index + 1)
                raw_pages.append(raw)
                pages.append(
                    Page(
                        index + 1,
                        native[index].rect.width,
                        native[index].rect.height,
                        [
                            Block(f"p{index + 1}-b{j}", "text", block.text, block.bbox, j)
                            for j, block in enumerate(raw, 1)
                        ],
                    )
                )
            title = PyMuPDFParser._clean_metadata_title(
                (native.metadata or {}).get("title"), str(path)
            )
            title = title or (
                PyMuPDFParser._guess_title_from_blocks(raw_pages[0]) if raw_pages else None
            )
            credit, found = extract_contributor_groups(raw_pages, pages, title)
        corpus.append(
            {
                "filename": path.name,
                "source_sha256": digest(path),
                "collective": credit,
                "groups": len(found),
            }
        )
    write_json(root / "corpus-front-sample.json", corpus)
    checks["no_other_corpus_front_matter_false_activation"] = [
        r["filename"] for r in corpus if r["groups"]
    ] == [source.name]
    checks["official_source_unchanged"] = digest(source) == source_hash
    report = {
        "status": "passed_scoped_checks" if all(checks.values()) else "failed",
        "runtime_source": inspect_source(project / "backend"),
        "checks": checks,
        "source_sha256": source_hash,
        "document_id": identifier,
        "chunks": count,
        "parser": PyMuPDFParser.version,
        "groups": [
            {
                "role": g["role"],
                "members": len(g["members"]),
                "physical_pages": [s["page_number"] for s in g["sources"]],
            }
            for g in groups.values()
        ],
        "scope": (
            "One fresh 73-page parse; first <=3 native text pages of 39 PDFs for false activation; "
            "not a full corpus reparse or all names' Unicode/affiliations accuracy"
        ),
        "llm_calls": 0,
        "bge_calls": 0,
        "production_library_modified": False,
        "baseline_comparison_requested": before is not None,
    }
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
