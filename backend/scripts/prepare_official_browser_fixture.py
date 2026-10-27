"""Create isolated browser fixtures; never reuse or clear the user's library."""

import argparse
import json
import sqlite3
from pathlib import Path

from official_functional_inputs import TEMP_ROOT, digest, isolated_output, load_inputs, write_json
from verify_official_local_features import GRAPH_FILES, configure, seed
from verify_official_output_features import select_sources


def saved_inputs(fixture: Path, filenames: list[str]) -> list[dict]:
    """Copy a SHA-pinned subset; read its old SQL metadata in read-only mode."""
    if not filenames or len(set(filenames)) != len(filenames):
        raise ValueError("Choose a nonempty unique saved-file subset")
    fixture = fixture.resolve()
    rows = select_sources(fixture, filenames)
    database = (fixture / "acceptance.db").resolve()
    if not database.is_relative_to(TEMP_ROOT) or not database.is_file():
        raise ValueError("Require an existing isolated fixture database")
    inputs = []
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in rows:
            source = fixture / "uploads" / (row["document_id"] + ".pdf")
            ast = fixture / "parsed" / (row["document_id"] + ".json")
            parsed = json.loads(ast.read_text(encoding="utf-8"))
            record = db.execute("SELECT * FROM documents WHERE id = ?",
                                (row["document_id"],)).fetchone()
            if (record is None or record["sha256"] != row["source_sha256"]
                    or record["original_filename"] != row["filename"]
                    or record["status"] != "ready"
                    or len(parsed["pages"]) != record["page_count"]
                    or not record["parser_name"] or not record["parser_version"]):
                raise ValueError("Saved source metadata and AST identity differ")
            inputs.append({
                "row": {"filename": row["filename"], "source_sha256": row["source_sha256"],
                        "size_bytes": source.stat().st_size, "title": parsed["title"],
                        "author_names": parsed["authors"], "page_count": len(parsed["pages"]),
                        "parser": record["parser_name"],
                        "parser_version": record["parser_version"]},
                "source": source, "ast": ast, "parsed": parsed, "ast_sha256": digest(ast),
            })
    return inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["graph", "versions", "saved-subset"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--parse-report", type=Path)
    parser.add_argument("--source-fixture", type=Path)
    parser.add_argument("--filenames", nargs="+")
    args = parser.parse_args()
    if args.mode == "saved-subset":
        if args.source_fixture is None or not args.filenames:
            parser.error("Saved subset requires --source-fixture and --filenames")
        inputs = saved_inputs(args.source_fixture, args.filenames)
    else:
        if args.corpus is None or args.parse_report is None:
            parser.error("Graph/version mode requires --corpus and --parse-report")
        inputs = load_inputs(args.corpus, args.parse_report)
    root = isolated_output(args.output)
    configure(root)
    if args.mode in {"graph", "saved-subset"}:
        selected = [item for item in inputs if item["row"]["filename"] in GRAPH_FILES]
        if args.mode == "graph" and len(selected) != 20:
            raise ValueError("Expected the original 20 distinct graph papers")
        if args.mode == "saved-subset":
            selected = inputs
        seed(root, selected)
    else:
        from app.core.config import settings
        from app.core.database import init_db

        settings.ensure_directories()
        init_db()
        write_json(root / "fixture.json", [])
    write_json(
        root / "preparation.json",
        {
            "status": "prepared",
            "mode": args.mode,
            "seeded_documents": len(selected) if args.mode != "versions" else 0,
            "cloud_calls": 0,
            "parser_calls": 0,
            "production_library_modified": False,
        },
    )
    print(f"Prepared isolated {args.mode} fixture")


if __name__ == "__main__":
    main()
