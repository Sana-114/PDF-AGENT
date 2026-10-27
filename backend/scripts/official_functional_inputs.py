"""Source-bound helpers for functional acceptance; never use the production library."""

import hashlib
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
TEMP_ROOT = (PROJECT / "backend" / "tmp").resolve()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def isolated_output(path: Path, *, resume: bool = False) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(TEMP_ROOT) or resolved == TEMP_ROOT:
        raise ValueError("Acceptance output must be a dedicated backend/tmp subdirectory")
    if resolved.exists() and not resume:
        raise ValueError("Output already exists; preserve previous acceptance runs")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def load_inputs(corpus: Path, report: Path) -> list[dict]:
    corpus = corpus.resolve()
    report = report.resolve()
    payload = json.loads(report.read_text(encoding="utf-8"))
    rows = payload["documents"]
    names = [row["filename"] for row in rows]
    if len(rows) != 39 or len(set(names)) != 39:
        raise ValueError("Expected the complete 39-file official parser report")
    if set(names) != {p.name for p in corpus.glob("*.pdf")}:
        raise ValueError("Parser report and corpus identities differ")
    inputs = []
    for row in rows:
        source = (corpus / row["filename"]).resolve()
        ast = (report.parent / row["artifacts_directory"] / "artifacts/parsed.json").resolve()
        if source.parent != corpus or not ast.is_relative_to(report.parent):
            raise ValueError("Unsafe input path")
        if row["status"] != "passed" or digest(source) != row["source_sha256"]:
            raise ValueError(f"Unverified source: {source.name}")
        parsed = json.loads(ast.read_text(encoding="utf-8"))
        if [p["page_number"] for p in parsed["pages"]] != list(range(1, row["page_count"] + 1)):
            raise ValueError(f"Incomplete AST: {source.name}")
        inputs.append(
            {"row": row, "source": source, "ast": ast, "parsed": parsed, "ast_sha256": digest(ast)}
        )
    return inputs
