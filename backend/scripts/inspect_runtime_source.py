"""Print a read-only fingerprint of deployed Python source and dependency pins."""

import hashlib
import json
import sys
from pathlib import Path


def inspect_source(root: Path) -> dict:
    records = []
    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        records.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    digest = hashlib.sha256(
        "".join(f"{path}\0{sha}\n" for path, sha in records).encode()
    ).hexdigest()
    return {
        "python_version": sys.version.split()[0],
        "app_files": len(records),
        "app_sha256": digest,
        "requirements_sha256": hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    print(json.dumps(inspect_source(Path(__file__).resolve().parents[1])))
