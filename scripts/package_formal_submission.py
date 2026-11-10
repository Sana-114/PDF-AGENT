"""Package a fixed Git commit and reviewed media; never publish or include local envs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import package_pre_submission as policy

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-11-10"
PDF = f"PaperPilot-formal-introduction-technical-{DATE}.pdf"
VIDEO = f"paperpilot-subtitled-complete-neural-v2-{DATE}"


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
    dest = ROOT / f"output/final-submission/{DATE}"
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
        policy.ROOT = snapshot
        policy.DOCS.discard("competition-technical-report.md")
        policy.DOCS.add(f"formal-submission-{DATE}.md")
        policy.RECORDING_E2E.add("recordFinalSupplements.mjs")
        selected = set(policy.select_source(files))
        selected.update(Path(p) for p in (
            "docs/assets/bilingual-20261110.png",
            "docs/assets/formal-comparison-20261110.png",
            "docs/assets/formal-graph-20261110.jpg",
        ))
        required = {"backend/app/main.py", "backend/requirements.txt", "frontend/package.json",
                    "frontend/package-lock.json", "scripts/start.ps1", "docker-compose.yml", *policy.TEMPLATES}
        assert required <= {p.as_posix() for p in selected}
        source = dest / f"02-source/PaperPilot-source-{DATE}.zip"
        source.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        with ZipFile(source, "w", ZIP_DEFLATED) as archive:
            for path in sorted(selected):
                data = (snapshot / ("docs/formal-source-readme.md" if path.as_posix() == "README.md" else path)).read_bytes()
                scan(data, path.as_posix())
                archive.writestr("PaperPilot-source/" + path.as_posix(), data)
                entries.append({"path": path.as_posix(), "bytes": len(data), "sha256": sha(data)})
        with ZipFile(source) as archive:
            assert archive.testzip() is None
        version = {
            "date": DATE, "git_commit": commit,
            "repository": "https://github.com/Sana-114/PDF-AGENT",
            "kind": "allowlisted committed source snapshot",
            "package_only_override": "README.md from committed docs/formal-source-readme.md",
            "source_files": len(entries), "source_zip_sha256": sha(source.read_bytes()),
            "external_submission_complete": False,
        }
        write("VERSION.json", json.dumps(version, ensure_ascii=False, indent=2).encode())
        manifest = {**version, "files": entries,
                    "omitted_paths": sorted(p.as_posix() for p in set(files) - selected)}
        write("02-source/source-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        for src, dst in (
            (f"docs/formal-submission-guide-{DATE}.md", "SUBMISSION-GUIDE.md"),
            (f"docs/formal-video-publishing-{DATE}.md", "03-video/publishing.md"),
        ):
            data = (snapshot / src).read_bytes()
            scan(data, src)
            write(dst, data)

    write("01-documents/" + PDF, (ROOT / "output/pdf" / PDF).read_bytes())
    for ext in ("mp4", "srt"):
        data = (ROOT / "output/video" / f"{VIDEO}.{ext}").read_bytes()
        if ext == "srt":
            scan(data, "subtitles")
        write(f"03-video/{VIDEO}.{ext}", data)

    reports = {}
    paths = {
        "backend_regression": "backend/tmp/demo-final-20261110/regression.xml",
        "reader_transformer": "backend/tmp/demo-final-20261110/reading-take6/report.json",
        "reader_bert": "backend/tmp/demo-final-20261110/reading-bert/report.json",
        "comparison": "backend/tmp/demo-final-20261110/take2/comparison/capture.json",
        "writing": "backend/tmp/demo-final-20261110/take2/writing/capture.json",
        "media": "backend/tmp/demo-final-20261110/closing-revision/complete-neural-v2/media-report.json",
    }
    for label, rel in paths.items():
        data = (ROOT / rel).read_bytes()
        entry = {"source": rel, "sha256": sha(data)}
        if rel.endswith(".xml"):
            suite = ET.fromstring(data).find("testsuite")
            entry["counts"] = {k: suite.attrib[k] for k in ("tests", "errors", "failures", "skipped")}
            assert entry["counts"] == {"tests": "1779", "errors": "0", "failures": "0", "skipped": "0"}
        else:
            report = json.loads(data)
            entry.update({k: report[k] for k in ("status", "mocked_responses", "checks", "duration_seconds", "width", "height") if k in report})
        reports[label] = entry
    summary = {
        "date": DATE,
        "note": "Summaries of existing local reports, not a new full-corpus run. Raw requests and papers excluded.",
        "reports": reports,
        "media_review": "Full decode and sampled frames checked; continuous subjective audio review remains.",
        "remaining": ["public deployment", "clean host build", "complex scanned equations",
                      "scan/native semantic duplicate miss", "broader layout coverage", "multi-user isolation"],
    }
    write("04-verification/acceptance-summary.json", json.dumps(summary, ensure_ascii=False, indent=2).encode())
    checks = []
    for path in sorted(dest.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.suffix in {".json", ".md", ".txt", ".srt"}:
            scan(data, path.name)
        checks.append(sha(data) + "  " + path.relative_to(dest).as_posix())
    write("SHA256SUMS.txt", ("\n".join(checks) + "\n").encode())
    bundle = dest.parent / f"PaperPilot-formal-submission-{DATE}.zip"
    with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
        for path in sorted(dest.rglob("*")):
            if path.is_file():
                archive.write(path, "PaperPilot-formal-submission/" + path.relative_to(dest).as_posix())
    with ZipFile(bundle) as archive:
        assert archive.testzip() is None
    bundle.with_suffix(".sha256").write_text(sha(bundle.read_bytes()) + "  " + bundle.name + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle), "bytes": bundle.stat().st_size,
                      "source_files": len(entries), "git_commit": commit}, ensure_ascii=False))


if __name__ == "__main__":
    main()
