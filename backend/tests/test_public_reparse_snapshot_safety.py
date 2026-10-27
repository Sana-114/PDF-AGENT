from pathlib import Path

import pytest

from scripts.snapshot_public_reparse import validate_output


def test_snapshot_only_accepts_new_dedicated_backup_directory(tmp_path):
    target = tmp_path / "acceptance-backups" / "public-document-before"
    assert validate_output(target, tmp_path) == target.resolve()


@pytest.mark.parametrize("target", [".", "acceptance-backups", "uploads", "../outside"])
def test_snapshot_rejects_broad_or_outside_paths(tmp_path, target):
    with pytest.raises(ValueError):
        validate_output(tmp_path / target, tmp_path)


def test_snapshot_preserves_prior_backup(tmp_path):
    previous = tmp_path / "acceptance-backups" / "previous"
    previous.mkdir(parents=True)
    with pytest.raises(ValueError):
        validate_output(Path(previous), tmp_path)
