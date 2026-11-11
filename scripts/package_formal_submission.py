"""Package a fixed Git commit and reviewed media; never publish or include local envs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import package_pre_submission as policy

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-11-10"
PACKAGE_DATE = "2026-11-11"
PDF = f"PaperPilot-formal-introduction-technical-{DATE}.pdf"
VIDEO = f"paperpilot-subtitled-complete-neural-v2-{DATE}"


def minimal_source(files, snapshot):
    """Start with app/tests/runtime, retaining auxiliary tools only when referenced."""
    selected = set()
    operational = {"start.ps1", "stop.ps1", "status.ps1", "check_runtime.ps1", "dev.ps1",
                   "deploy.sh", "verify_public_demo.sh", "test_verify_public_demo.sh"}
    docs = {"architecture.md", "local-bge.md", "public-deployment.md", "rag-evaluation.md"}
    for p in files:
        if p.parts[0] == "docs":
            keep = len(p.parts) == 2 and p.name in docs
        elif p.parts[0] == "scripts":
            keep = p.name in operational
        elif p.parts[:2] == ("backend", "scripts"):
            keep = p.name in {"__init__.py", "check_bge_services.py", "reindex_vectors.py"}
        elif p.parts[:2] == ("frontend", "e2e"):
            keep = p.name not in policy.RECORDING_E2E | {"recordFinalSupplements.mjs", "bilingualReadiness.mjs", "generationReadiness.mjs"}
        else:
            keep = p.name not in {"docker-compose.recording.yml", "docker-compose.recording-models.yml", "requirements-docs.txt"}
        if keep:
            selected.add(p)
    pool = {p for p in files if p.parts[:2] in {("backend", "scripts"), ("docs", "evidence"), ("frontend", "e2e")}
            or p.parts[0] == "scripts" or (p.parts[0] == "docs" and p.suffix == ".json")}
    while True:
        texts = "\n".join((snapshot / p).read_text(encoding="utf-8-sig") for p in selected
                          if p.suffix in {".py", ".mjs", ".ps1", ".sh", ".json", ".yml", ".yaml"})
        added = {p for p in pool - selected if p.name in texts
                 or (p.suffix == ".py" and re.search(r"\b" + re.escape(p.stem) + r"\b", texts))}
        if not added:
            return selected
        selected.update(added)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], cwd=ROOT
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    commit = git("rev-parse", "--verify", args.ref + "^{commit}").decode().strip()
    dest = ROOT / f"output/final-submission/{PACKAGE_DATE}-clean"
    if dest.exists():
        raise ValueError("Destination already exists; preserve it or move it before rebuilding")

    known_secrets = []
    for name in (".env", ".env.bge", ".env.production"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            if key.strip() == "MINIO_SECRET_KEY":
                template = (ROOT / ".env.example").read_text(encoding="utf-8")
                if line in template.splitlines():
                    continue
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.I) and len(value) >= 16:
                known_secrets.append(value.encode())
    pattern = re.compile(rb"sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{25,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----")

    def scan(data, label):
        if pattern.search(data) or any(value in data for value in known_secrets):
            raise ValueError(f"Credential-like content; packaging stopped: {label}")

    def write(rel, data):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    temp_root = ROOT / "tmp"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="formal-snapshot-", dir=temp_root) as tmp:
        snapshot = Path(tmp)
        raw = snapshot / "snapshot.zip"
        git("archive", "--format=zip", "--output=" + str(raw), commit)
        with ZipFile(raw) as archive:
            files = []
            for item in archive.infolist():
                path = Path(item.filename)
                if item.is_dir() or not policy.allowed(path):
                    continue
                target = (snapshot / path).resolve()
                if not target.is_relative_to(snapshot.resolve()):
                    raise ValueError("Unsafe snapshot entry")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                files.append(path)
        selected = minimal_source(files, snapshot)
        required = {"backend/app/main.py", "backend/requirements.txt", "frontend/package.json",
                    "frontend/package-lock.json", "scripts/start.ps1", "docker-compose.yml", *policy.TEMPLATES}
        assert required <= {p.as_posix() for p in selected}
        source = dest / "PaperPilot-source.zip"
        source.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        with ZipFile(source, "w", ZIP_DEFLATED) as archive:
            for path in sorted(selected):
                data = (snapshot / ("docs/formal-source-readme.md" if path.as_posix() == "README.md" else path)).read_bytes()
                if path.as_posix() == "README.md":
                    data += f"\n源码基准提交：{commit}\n".encode()
                scan(data, path.as_posix())
                archive.writestr("PaperPilot-source/" + path.as_posix(), data)
                entries.append({"path": path.as_posix(), "bytes": len(data), "sha256": sha(data)})
        with ZipFile(source) as archive:
            assert archive.testzip() is None
        version = {
            "date": PACKAGE_DATE, "git_commit": commit,
            "repository": "https://github.com/Sana-114/PDF-AGENT",
            "kind": "allowlisted committed source snapshot",
            "package_only_override": "README.md from committed docs/formal-source-readme.md",
            "source_files": len(entries), "source_zip_sha256": sha(source.read_bytes()),
            "external_submission_complete": False,
        }
        manifest = {**version, "files": entries,
                    "omitted_paths": sorted(p.as_posix() for p in set(files) - selected)}
        # Audit details are local build records, not reviewer-facing deliverables.
        audit = ROOT / f"tmp/formal-package-{PACKAGE_DATE}"
        audit.mkdir(parents=True, exist_ok=True)
        (audit / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    write("PaperPilot-introduction.pdf", (ROOT / "output/pdf" / PDF).read_bytes())
    write("PaperPilot-demo.mp4", (ROOT / "output/video" / f"{VIDEO}.mp4").read_bytes())
    guide = (
        "PaperPilot 正式提交材料（精简版）\n\n"
        "1. PaperPilot-introduction.pdf：作品介绍与技术文档，8 页；直接上传作品介绍栏。\n"
        "2. PaperPilot-source.zip：源码、环境模板、启动部署配置及测试；解压后按 README.md 运行。\n"
        "3. PaperPilot-demo.mp4：约 6 分 24 秒、1080p、普通话配音、内嵌中文字幕。\n\n"
        "完整作品链接：将本材料 ZIP 上传网盘，填写长期有效链接及提取码。\n"
        "演示视频地址：将 MP4 上传 B站等公开视频平台，填写播放页链接，不填网盘链接。\n"
        "公网演示尚未部署，按原赛题仍需上线验收；团队及个人署名暂按项目方要求省略。\n"
        "本次仅精简提交包装，不改变业务代码或新增模型验收结论。\n\n"
        f"源码基准提交：{commit}\n"
        "Git 历史：https://github.com/Sana-114/PDF-AGENT\n"
        "源码 README 使用提交版说明，除此之外所选源码直接来自上述提交。\n"
    )
    write("提交说明.txt", guide.encode("utf-8-sig"))
    checks = []
    for path in sorted(dest.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.suffix in {".json", ".md", ".txt", ".srt"}:
            scan(data, path.name)
        checks.append(sha(data) + "  " + path.relative_to(dest).as_posix())
    (audit / "SHA256SUMS.txt").write_text("\n".join(checks) + "\n", encoding="utf-8")
    bundle = dest.parent / f"PaperPilot-formal-submission-clean-{PACKAGE_DATE}.zip"
    with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
        for path in sorted(dest.rglob("*")):
            if path.is_file():
                archive.write(path, "PaperPilot-formal-submission/" + path.relative_to(dest).as_posix())
    with ZipFile(bundle) as archive:
        assert archive.testzip() is None
    (audit / "bundle.sha256").write_text(sha(bundle.read_bytes()) + "  " + bundle.name + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle), "bytes": bundle.stat().st_size,
                      "source_files": len(entries), "git_commit": commit}, ensure_ascii=False))


if __name__ == "__main__":
    main()
