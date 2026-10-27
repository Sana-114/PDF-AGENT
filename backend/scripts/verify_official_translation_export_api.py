"""Install accepted translations into an isolated fixture and test actual export routes."""

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

import httpx
from official_functional_inputs import TEMP_ROOT, digest, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8011")
    parser.add_argument("--recheck", action="store_true")
    args = parser.parse_args()
    root, translation = args.fixture.resolve(), args.translation.resolve()
    if not all(p.is_relative_to(TEMP_ROOT) and p != TEMP_ROOT for p in (root, translation)):
        raise ValueError("Requires isolated test output directories")
    url = urlparse(args.api)
    if url.hostname not in {"localhost", "127.0.0.1"} or url.port in {8000, 80, 443}:
        raise ValueError("Requires the isolated loopback acceptance API, not production")
    report = json.loads((translation / "report.json").read_text(encoding="utf-8"))
    if report["status"] != "passed":
        raise ValueError("Only install translations with passed coverage checks")
    doc_id = report["document_id"]
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    entry = next(x for x in fixture if x["document_id"] == doc_id)
    for key in ("source_sha256", "ast_sha256"):
        if entry[key] != report["identity"][key]:
            raise ValueError("Translation and fixture identities differ")
    if digest(root / "parsed" / (doc_id + ".json")) != entry["ast_sha256"]:
        raise ValueError("Fixture AST changed")
    destination = root / "translations" / doc_id
    if destination.exists() and not args.recheck:
        raise ValueError("Preserve previously installed translation data")
    if not args.recheck:
        shutil.copytree(translation / "translations" / doc_id, destination)
    else:
        for source in (translation / "translations" / doc_id).rglob("*.json"):
            installed = destination / source.relative_to(translation / "translations" / doc_id)
            if not installed.is_file() or digest(source) != digest(installed):
                raise ValueError("Installed translation data changed")
    checks = []
    with httpx.Client(base_url=args.api, timeout=60, trust_env=False) as client:
        for number in range(1, 16):
            response = client.get(f"/api/v1/documents/{doc_id}/translations/zh/pages/{number}")
            expected = json.loads(
                (destination / "zh" / f"page-{number:06d}.json").read_text(encoding="utf-8")
            )
            checks.append(
                {
                    "type": "page",
                    "page": number,
                    "passed": response.status_code == 200 and response.json() == expected,
                }
            )
        for export in report["exports"]:
            response = client.get(
                f"/api/v1/documents/{doc_id}/translations/zh/export",
                params={"format": export["format"], "mode": export["mode"]},
            )
            expected = Path(export["path"]).read_text(encoding="utf-8")
            checks.append(
                {
                    "type": "export",
                    "format": export["format"],
                    "mode": export["mode"],
                    "passed": response.status_code == 200
                    and response.text == expected
                    and "attachment;" in response.headers.get("content-disposition", ""),
                }
            )
    result = {
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "cloud_calls": 0,
        "checks": checks,
        "document_id": doc_id,
    }
    report_path = root / "translation-export-api.json"
    if report_path.exists():
        index = len(list(root.glob("translation-export-api*.json"))) + 1
        report_path = root / f"translation-export-api-{index:03d}.json"
    write_json(report_path, result)
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
