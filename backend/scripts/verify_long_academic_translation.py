"""Translate a public-data research note through the workbench API in an isolated library."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from official_functional_inputs import PROJECT, digest, isolated_output, write_json
from verify_official_output_features import configure

SOURCE = PROJECT / "backend/tests/fixtures/academic_paper_zh.md"
EVIDENCE = PROJECT / "docs/evidence/official-screening-2026-11-06.json"
TERMS = {
    "正文指纹": "text fingerprint", "原文锚点": "source anchor", "引用用途": "citation function",
}


def paragraphs(text):
    # Blank lines inside the Python listing belong to one paragraph.
    normalized = re.sub(r"```[\s\S]*?```", lambda m: m.group().replace("\n\n", "\n"), text)
    return re.split(r"\n\s*\n", normalized.strip())


def run(args):
    if not args.allow_cloud:
        raise ValueError("Pass --allow-cloud to translate the bundled public-data note")
    root = isolated_output(args.output)
    configure(root)
    from fastapi.testclient import TestClient

    from app.main import app

    source = SOURCE.read_text(encoding="utf-8").strip()
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert (evidence["source_count"], evidence["pairs"], evidence["positives"],
            len(evidence["false_negatives"])) == (39, 741, 4, 1)
    payload = {
        "text": source, "document_type": "paper", "source_language": "zh", "target_language": "en",
        "glossary": [{"source": s, "target": t} for s, t in TERMS.items()],
    }
    report = {"input_sha256": digest(SOURCE), "evidence_sha256": digest(EVIDENCE),
              "characters": len(source), "paragraphs": len(paragraphs(source)), "checks": {}}
    checks = report["checks"]
    with TestClient(app) as client:
        response = client.post("/api/v1/writing/translate", json=payload)
        report["http"] = response.status_code
        write_json(root / "translation.json", response.json())
        write_json(root / "report.json", report)
        if not response.is_success:
            print(f"Translation HTTP {response.status_code}")
            return 1
        result = response.json()
        translation = result["translation"]
        report.update(provider=result["provider"], model=result["model"],
                      batches=result["request_count"])
        checks["real_multibatch"] = result["provider"] == "deepseek" and result["request_count"] > 1
        original, english = paragraphs(source), paragraphs(translation)
        checks["paragraphs"] = len(original) == len(english) == result["paragraph_count"]
        checks["english_prose"] = not re.search(r"[\u3400-\u9fff]", translation)
        checks["no_tokens"] = "__PAPERPILOT_" not in translation
        checks["verbatim_code"] = (re.findall(r"```[\s\S]*?```", source)
                                   == re.findall(r"```[\s\S]*?```", translation))
        term_counts = {t["source"]: t["count"] for t in result["glossary_applied"]}
        checks["glossary_counts"] = term_counts == {term: source.count(term) for term in TERMS}
        # Check each source position independently, not just document-wide counts.
        rows = []
        for index, (src, dst) in enumerate(zip(original, english, strict=False), start=1):
            literals = re.findall(r"```[\s\S]*?```|`[^`]+`|\$[^$]+\$|\[\d+\]|\d+(?:\.\d+)?", src)
            literals.extend(TERMS[t] for t in TERMS for _ in range(src.count(t)))
            missing = [s for s in set(literals) if dst.count(s) < literals.count(s)]
            rows.append({"paragraph": index, "missing": sorted(missing)})
        checks["protected_per_paragraph"] = checks["paragraphs"] and not any(
            r["missing"] for r in rows
        )
        report["mismatches"] = [r for r in rows if r["missing"]]
        draft = client.post("/api/v1/writing/drafts", json={
            "title": "官方测试数据研究札记", "source_text": source, "translated_text": translation,
            "source_language": "zh", "target_language": "en", "document_type": "paper",
            "provider": result["provider"], "model": result["model"],
        })
        drafts = client.get("/api/v1/writing/drafts").json()["items"]
        checks["draft_roundtrip"] = draft.status_code == 201 and any(
            d["id"] == draft.json()["id"] and d["source_text"] == source
            and d["translated_text"] == translation for d in drafts
        )
    export_input = root / "export-input.json"
    write_json(export_input, {"title": "Official Corpus Study", "source": source,
                              "translation": translation, "status": "draft",
                              "sourceLanguage": "zh", "targetLanguage": "en"})
    subprocess.run([
        "node", "--input-type=module", "-e",
        "import fs from 'node:fs';"
        "import {buildTranslationMarkdown} from './lib/academicTranslation.ts';"
        "const data=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));"
        "fs.writeFileSync(process.argv[2], buildTranslationMarkdown(data));",
        str(export_input), str(root / "translation.md"),
    ], cwd=PROJECT / "frontend", check=True)
    markdown = (root / "translation.md").read_text(encoding="utf-8")
    checks["markdown_roundtrip"] = markdown.endswith(
        f"## Source\n\n{source}\n\n## Academic Translation\n\n{translation}\n"
    )
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return int(not all(checks.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-cloud", action="store_true")
    raise SystemExit(run(parser.parse_args()))
