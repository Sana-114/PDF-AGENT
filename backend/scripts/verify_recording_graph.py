"""Verify a live 20-paper recording graph against SHA-pinned original PDF pages."""
import argparse
import json
from pathlib import Path

import httpx

from audit_recording_saved_outputs import digest, source_pdf
from verify_official_local_features import verify_graph
from verify_official_runtime_reader import validate_api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8024")
    args = parser.parse_args()
    root, output = args.run.resolve(), args.output.resolve()
    temporary = Path(__file__).resolve().parents[1] / "tmp"
    if not all(p.is_relative_to(temporary) for p in (root, output)):
        raise ValueError("Only isolated workspace artifacts are allowed")
    api = validate_api(args.api)
    output.mkdir(exist_ok=False, parents=True)
    rows = json.loads((root / "data/fixture.json").read_text(encoding="utf-8"))
    if len(rows) != 20 or len({row["document_id"] for row in rows}) != 20:
        raise ValueError("Requires twenty distinct pinned papers")
    inputs = []
    for row in rows:
        if Path(row["document_id"]).name != row["document_id"]:
            raise ValueError("Invalid document identity")
        ast = root / "data/parsed" / (row["document_id"] + ".json")
        if digest(ast) != row["ast_sha256"]:
            raise ValueError("Parsed source changed")
        parsed = json.loads(ast.read_text(encoding="utf-8"))
        row["title"] = parsed["title"]
        inputs.append({"row": row, "document_id": row["document_id"],
                       "source": source_pdf(root, row), "parsed": parsed})
    with httpx.Client(base_url=api, trust_env=False, timeout=120) as client:
        result = verify_graph(client, inputs, {i["document_id"]: i for i in inputs}, output)
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "edge_audit"}))
    return result["status"] != "passed"


if __name__ == "__main__":
    raise SystemExit(main())
