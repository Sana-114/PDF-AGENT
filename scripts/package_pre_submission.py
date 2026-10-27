"""Create an allowlisted working-tree snapshot and dated pre-submission bundle.

No uploads or Git mutations. Credentials, runtime data and third-party PDFs are excluded.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "output/pre-submission/2026-10-27-clean"
TEMPLATES = {".env.example", ".env.bge.example", ".env.production.example"}
EXTENSIONS = {".py", ".ps1", ".mjs", ".js", ".ts", ".tsx", ".css", ".json", ".md",
              ".txt", ".toml", ".yml", ".yaml", ".sh", ".conf", ".csv", ".html", ".svg"}
SPECIAL = {"Dockerfile", "Caddyfile", ".dockerignore", ".gitignore", ".gitattributes", "LICENSE"}
ROOT_SCRIPTS = {
    "start.ps1", "stop.ps1", "status.ps1", "check_runtime.ps1", "dev.ps1",
    "deploy.sh", "verify_public_demo.sh", "test_verify_public_demo.sh",
    "fetch_pdf_corpus.ps1", "run_grounded_rag_e2e.ps1", "run_real_pdf_browser_e2e.ps1",
    "run_real_pdf_comparison_e2e.ps1", "run_release_pdf_acceptance.ps1", "run_system_e2e.ps1",
}
DOCS = {"architecture.md", "public-deployment.md", "local-bge.md", "rag-evaluation.md",
        "competition-technical-report.md", "official-pdf-manifest.json", "pdf-regression-corpus.json"}
RECORDING_E2E = {"recordDemoEndCard.mjs", "recordingReadiness.mjs", "recordingSmoke.mjs", "recordSubtitledDemo.mjs"}


def select_source(files: list[Path]) -> list[Path]:
    """Retain application/tests, then conservatively close local tool/fixture references.

    Basename and module-token matches deliberately retain uncertain dependencies;
    this is a package filter, not a claim that excluded tools are dead code.
    """
    selected = set()
    for path in files:
        parts = path.parts
        if parts[0] == "scripts":
            keep = path.name in ROOT_SCRIPTS
        elif parts[0] == "docs":
            keep = (len(parts) == 2 and path.name in DOCS) or parts[:3] == ("docs", "assets", "recording")
        elif parts[:2] == ("backend", "scripts"):
            keep = (path.name.startswith(("verify_", "evaluate_")) or path.name in {
                "__init__.py", "reindex_vectors.py", "check_bge_services.py",
                "prepare_official_all_corpus.py", "audit_official_reference_styles.py",
            })
        elif parts[:2] == ("frontend", "e2e"):
            keep = path.name not in RECORDING_E2E
        else:
            keep = path.name not in {"docker-compose.recording.yml", "docker-compose.recording-models.yml", "requirements-docs.txt"}
        if keep:
            selected.add(path)
    pool = {p for p in files if p.parts[:2] in {("backend", "scripts"), ("docs", "evidence")}
            or p.parts[:2] == ("frontend", "e2e")}
    while True:
        texts = "\n".join((ROOT / p).read_text(encoding="utf-8-sig") for p in selected
                          if p.suffix in {".py", ".mjs", ".ps1", ".sh", ".json"})
        added = {p for p in pool - selected if p.name in texts
                 or (p.suffix == ".py" and re.search(r"\b" + re.escape(p.stem) + r"\b", texts))}
        if not added:
            return sorted(selected)
        selected.update(added)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], cwd=ROOT)


def allowed(path: Path) -> bool:
    parts = path.parts
    if any(p in {"tmp", "data", ".git", "node_modules", "__pycache__"} or p.startswith(".next") for p in parts):
        return False
    if path.name.startswith(".env"):
        return len(parts) == 1 and path.name in TEMPLATES
    if len(parts) > 1 and parts[0] not in {"backend", "frontend", "scripts", "docs", "deploy"}:
        return False
    if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return parts[:2] == ("docs", "assets")
    return path.suffix.lower() in EXTENSIONS or path.name in SPECIAL


def copy(src: str, dst: str):
    target = DEST / dst
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / src, target)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    known_secrets = []
    # This development-only MinIO default is already shipped in the template;
    # it is not an API credential. Do not suppress any other secret matches.
    defaults = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
                    if "=" in line and not line.lstrip().startswith("#"))
    for env in [ROOT / ".env", ROOT / ".env.bge", ROOT / ".env.production"]:
        if env.exists():
            for line in env.read_text(encoding="utf-8-sig").splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                name, value = line.split("=", 1)
                value = value.strip().strip("\"'")
                if name.strip() == "MINIO_SECRET_KEY" and value == defaults.get("MINIO_SECRET_KEY"):
                    continue
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD", name, re.I) and len(value) >= 16:
                    known_secrets.append(value.encode())
    forbidden = re.compile(rb"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{25,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)")

    def scan(data: bytes, name: str):
        if any(secret in data for secret in known_secrets) or forbidden.search(data):
            raise ValueError(f"Credential-like content found; packaging stopped: {name}")

    candidates = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    available = sorted({Path(p) for p in candidates if p and allowed(Path(p)) and (ROOT / p).is_file()})
    files = select_source(available)
    required = {"backend/app/main.py", "backend/requirements.txt", "frontend/package-lock.json",
                "frontend/package.json", "scripts/start.ps1", "docker-compose.yml", *TEMPLATES}
    missing = required - {p.as_posix() for p in files}
    if missing:
        raise ValueError(f"Missing required source files: {sorted(missing)}")
    source_dir = DEST / "02-source"
    source_dir.mkdir(exist_ok=True)
    source_zip = source_dir / "PaperPilot-source-2026-10-27.zip"
    entries = []
    with ZipFile(source_zip, "w", ZIP_DEFLATED) as archive:
        for path in files:
            actual = ROOT / path
            if actual.is_symlink() or not actual.resolve().is_relative_to(ROOT):
                raise ValueError(f"Unexpected source link: {path}")
            data = ((ROOT / "docs/pre-submission-source-readme.md").read_bytes()
                    if path.as_posix() == "README.md" else actual.read_bytes())
            if path.as_posix() == "docs/competition-technical-report.md":
                data = ("对评审的说明：本文保留技术方案及历史测试口径。当前 2026-10-27 预提交状态以材料包中的作品介绍和验收汇总为准；团队及个人署名暂不填写。\n\n".encode() + data)
            scan(data, path.as_posix())
            archive.writestr("PaperPilot-source/" + path.as_posix(), data)
            entries.append({"path": path.as_posix(), "bytes": len(data), "sha256": digest(data)})
    with ZipFile(source_zip) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(entries)
    version = {
        "kind": "working-tree snapshot, not a frozen release",
        "date": "2026-10-27", "git_head": git("rev-parse", "HEAD").decode().strip(),
        "git_branch": git("branch", "--show-current").decode().strip(),
        "has_uncommitted_changes": bool(git("status", "--porcelain")),
        "repository": "https://github.com/Sana-114/PDF-AGENT",
        "source_files": len(entries), "source_zip_sha256": digest(source_zip.read_bytes()),
        "excluded": ["real env files", "Git internals", "models", "runtime data", "official PDFs", "raw provider responses",
                     "historical stage reports", "superseded drafts", "media authoring tools", "unreferenced diagnostic tools"],
        "omitted_source_paths": [p.as_posix() for p in available if p not in files],
        "package_only_overrides": ["README.md: focused submission readme", "docs/competition-technical-report.md: dated scope notice"],
        "files": entries,
    }
    (source_dir / "source-manifest.json").write_text(json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")
    (source_dir / "VERSION.txt").write_text(
        "当前工作目录快照，含未提交修复。Git HEAD: " + version["git_head"] +
        "\n精简提交包：省略历史资料和材料制作工具；保留应用与测试依赖。README 使用提交版说明。\n不等于仓库 main，也不代表当前运行镜像逐字一致。源码历史见仓库，实际内容按 source-manifest.json 核对。\n", encoding="utf-8")
    copy("output/pdf/PaperPilot-pre-submission-introduction-2026-10-27.pdf",
         "01-introduction/PaperPilot-pre-submission-introduction-2026-10-27.pdf")
    copy("docs/pre-submission-introduction-2026-10-27.md", "01-introduction/introduction.md")
    for path in (ROOT / "docs/assets/pre-submission").glob("*.png"):
        copy(path.relative_to(ROOT).as_posix(), "01-introduction/docs/assets/pre-submission/" + path.name)
    for ext in ["mp4", "srt"]:
        name = f"paperpilot-subtitled-conversational-v3-2026-10-27.{ext}"
        copy("output/video/" + name, "03-video/" + name)
    copy("docs/demo-narration-2026-10-27.md", "03-video/narration.md")
    copy("docs/demo-video-revision-2026-10-27.md", "03-video/video-scope.md")
    copy("docs/pre-submission-guide-2026-10-27.md", "START-HERE.md")

    # Preserve the audit trail without redistributing full requests, excerpts or local identifiers.
    sources = {
        "backend_regression": "backend/tmp/official-runtime-resume-2026-10-25/oct26-subtitled-relations/regression.xml",
        "math_review": "backend/tmp/subtitled-demo-2026-10-26/math-review.json",
        "saved_evidence_qa": "backend/tmp/subtitled-demo-2026-10-26/saved-policy.json",
        "new_bge_qa": "backend/tmp/subtitled-demo-2026-10-26/live-rag/report.json",
        "reader": "backend/tmp/official-completion-2026-10-26/reader-browser/report.json",
        "future_browser": "backend/tmp/official-completion-2026-10-26/future-browser-final/report.json",
        "graph": "backend/tmp/official-runtime-resume-2026-10-25/graph-browser-final/report.json",
        "media": "backend/tmp/subtitled-demo-2026-10-27/conversational-v3/media-report.json",
    }
    evidence = {"note": "Derived summaries of prior reports, not new test executions. Original hashes are for provenance; raw reports remain local.", "reports": {}}
    for name, rel in sources.items():
        data = (ROOT / rel).read_bytes()
        item = {"source": rel, "sha256": digest(data)}
        if rel.endswith(".xml"):
            suite = ET.fromstring(data).find("testsuite")
            item["counts"] = {k: suite.attrib[k] for k in ["tests", "errors", "failures", "skipped"]}
            assert item["counts"] == {"tests": "1746", "errors": "0", "failures": "0", "skipped": "0"}
        else:
            report = json.loads(data)
            item.update({k: report[k] for k in ["status", "scope", "automatic_retries", "generation_requests", "width", "height", "duration_seconds"] if k in report})
            if isinstance(report.get("checks"), list):
                item["checks"] = [{k: c[k] for k in ["name", "passed"] if k in c} for c in report["checks"] if isinstance(c, dict)]
        evidence["reports"][name] = item
    evidence_dir = DEST / "04-evidence"
    evidence_dir.mkdir(exist_ok=True)
    (evidence_dir / "acceptance-summary.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    copy("docs/pre-submission-cleanup-check.md", "04-evidence/package-check.md")

    sums = []
    for path in sorted(DEST.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            data = path.read_bytes()
            if path.suffix not in {".zip", ".mp4", ".pdf", ".png"}:
                scan(data, path.relative_to(DEST).as_posix())
            sums.append(digest(data) + "  " + path.relative_to(DEST).as_posix())
    (DEST / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    bundle = DEST.parent / "PaperPilot-pre-submission-clean-2026-10-27.zip"
    with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
        for path in sorted(DEST.rglob("*")):
            if path.is_file():
                archive.write(path, "PaperPilot-pre-submission/" + path.relative_to(DEST).as_posix())
    with ZipFile(bundle) as archive:
        assert archive.testzip() is None
    (bundle.with_suffix(".sha256.txt")).write_text(digest(bundle.read_bytes()) + "  " + bundle.name + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle), "bundle_bytes": bundle.stat().st_size,
                      "source_files": len(entries), "source_zip_bytes": source_zip.stat().st_size,
                      "checks": "allowlist, secret-pattern and local-secret scan, required files, ZIP CRC"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
