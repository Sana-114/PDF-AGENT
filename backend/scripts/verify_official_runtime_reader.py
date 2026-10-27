"""Recheck audited official ASTs through a real isolated container's GET APIs.

Never seed the production database or call generation routes. This verifies
serving and anchor integrity, not the scientific correctness of all parsed text.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from verify_official_local_features import verify_reader


def validate_api(value: str) -> str:
    url = urlparse(value)
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1"}
        or url.username is not None
        or url.password is not None
        or url.port is None
        or not 8011 <= url.port <= 8099
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("Requires a dedicated loopback acceptance API, never production")
    return value.rstrip("/")


def fixture_inputs(root: Path, expected_count: int = 39) -> list[dict]:
    root = root.resolve()
    if not root.is_relative_to(TEMP_ROOT) or root == TEMP_ROOT:
        raise ValueError("Only isolated official fixtures are allowed")
    rows = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    if not 1 <= expected_count <= 39:
        raise ValueError("Expected count must be between one and 39")
    if len(rows) != expected_count or len({row["document_id"] for row in rows}) != expected_count:
        raise ValueError(f"Expected exactly {expected_count} unique audited fixture files")
    inputs = []
    for row in rows:
        source = (root / "uploads" / (row["document_id"] + ".pdf")).resolve()
        ast = (root / "parsed" / (row["document_id"] + ".json")).resolve()
        if not source.is_relative_to(root / "uploads") or not ast.is_relative_to(root / "parsed"):
            raise ValueError("Unsafe fixture identity")
        if digest(source) != row["source_sha256"] or digest(ast) != row["ast_sha256"]:
            raise ValueError("Fixture PDF or AST changed")
        inputs.append(
            {
                "row": row,
                "document_id": row["document_id"],
                "source": source,
                "parsed": json.loads(ast.read_text(encoding="utf-8")),
                "ast_sha256": row["ast_sha256"],
            }
        )
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--api", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=39,
                        help="Explicit subset size; default remains the complete 39-file gate")
    args = parser.parse_args()
    api = validate_api(args.api)
    inputs = fixture_inputs(args.fixture, args.expected_count)
    output = isolated_output(args.output)
    report = {
        "status": "running",
        "api": api,
        "scope": (
            "Real HTTP serving, persisted AST and navigation anchors; "
            "not full extraction accuracy"
        ),
        "cloud_calls": 0,
        "production_library_modified": False,
        "automatic_retries": 0,
        "fixture_sha256": digest(args.fixture / "fixture.json"),
        "expected_files": args.expected_count,
        "files": [],
        "errors": [],
    }
    try:
        with httpx.Client(base_url=api, timeout=60, trust_env=False) as client:
            health = client.get("/api/v1/health")
            health.raise_for_status()
            for item in inputs:
                result = verify_reader(client, item)
                report["files"].append(result)
                write_json(output / "report.json", report)
                print(f"{result['filename']}: {result['status']}", flush=True)
        report["status"] = (
            "passed" if all(row["status"] == "passed" for row in report["files"]) else "failed"
        )
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(str(exc))
    report["summary"] = {
        "executed": len(report["files"]),
        "passed": sum(row["status"] == "passed" for row in report["files"]),
        "pages": sum(row["pages"] for row in report["files"]),
    }
    write_json(output / "report.json", report)
    print(json.dumps(report["summary"]), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
