"""Exercise diagram API exports and run generated Matplotlib scripts, no LLM.

Inputs are synthetic topology specifications, not inferred paper architectures.
Uninstalled compilers are reported unavailable, never counted as renderer passes.
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from verify_official_output_features import configure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = isolated_output(args.output_dir)
    configure(root)
    os.environ.update(
        LLM_PROVIDER="mock",
        LLM_API_KEY="",
        EMBEDDING_PROVIDER="hash",
        VECTOR_SEARCH_ENABLED="false",
        RERANKER_PROVIDER="none",
    )
    from fastapi.testclient import TestClient
    from PIL import Image

    from app.main import app

    cases = [
        (
            "chinese",
            "科研助手流程",
            "PDF输入 -> 版式解析 -> 原文索引\n"
            "问题 -> 混合检索 -> 引用核验 -> 回答\n原文索引 -> 混合检索",
            "left-to-right",
        ),
        ("escaped", "R&D_架构", 'PDF_输入 -> "Parser" & Guard -> Output#1', "top-down"),
        ("cycle", "Feedback cycle", "Input -> Review -> Output\nOutput -> Review", "left-to-right"),
    ]
    report = {
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "cases": [],
        "cloud_calls": 0,
        "BGE_calls": 0,
        "synthetic_inputs": True,
        "production_library_modified": False,
        "external_compilers": {name: shutil.which(name) for name in ("dot", "xelatex", "mmdc")},
    }
    with TestClient(app) as client:
        for case_id, title, idea, layout in cases:
            folder = root / case_id
            folder.mkdir()
            response = client.post(
                "/api/v1/diagrams/generate",
                json={
                    "title": title,
                    "idea": idea,
                    "layout": layout,
                    "formats": ["mermaid", "graphviz", "tikz", "matplotlib"],
                },
            )
            response.raise_for_status()
            payload = response.json()
            write_json(folder / "response.json", payload)
            scripts = {s["format"]: s for s in payload["scripts"]}
            for script in scripts.values():
                (folder / script["filename"]).write_text(script["content"], encoding="utf-8")
            canonical = {(e["source"], e["target"]) for e in payload["edges"]}
            patterns = {
                "mermaid": r"(n\d+) --> (n\d+)",
                "graphviz": r"(n\d+) -> (n\d+);",
                "tikz": r"\\draw\[flow\] \((n\d+)\) -- \((n\d+)\);",
            }
            checks = {"api_200": response.status_code == 200}
            for fmt, pattern in patterns.items():
                checks[fmt + "_edges_match_canonical"] = (
                    set(re.findall(pattern, scripts[fmt]["content"])) == canonical
                )
            tree = ast.parse(scripts["matplotlib"]["content"])
            allowed = {"matplotlib", "warnings"}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(n.name.split(".")[0] in allowed for n in node.names)
                elif isinstance(node, ast.ImportFrom):
                    assert node.module.split(".")[0] in allowed
            env = {**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run(
                [sys.executable, scripts["matplotlib"]["filename"]],
                cwd=folder,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
                check=False,
            )
            (folder / "render-stdout.txt").write_text(result.stdout, encoding="utf-8")
            (folder / "render-stderr.txt").write_text(result.stderr, encoding="utf-8")
            image = folder / "research-architecture.png"
            checks["matplotlib_exit_zero"] = result.returncode == 0
            checks["no_missing_glyph_warning"] = not re.search(
                r"Glyph .* missing|missing from font", result.stderr
            )
            dimensions = None
            if image.exists():
                with Image.open(image) as png:
                    dimensions = list(png.size)
                    checks["nonempty_raster"] = png.width > 400 and png.height > 200
            else:
                checks["nonempty_raster"] = False
            report["cases"].append(
                {
                    "case_id": case_id,
                    "checks": checks,
                    "dimensions": dimensions,
                    "renderer_exit_code": result.returncode,
                }
            )
    report["status"] = (
        "passed_matplotlib_and_graph_contracts"
        if all(all(c["checks"].values()) for c in report["cases"])
        else "failed"
    )
    report["all_four_compilers_verified"] = False
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {k: report[k] for k in ("status", "cases", "external_compilers")}, ensure_ascii=False
        )
    )
    return 0 if report["status"].startswith("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
