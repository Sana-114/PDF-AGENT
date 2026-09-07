import json

import fitz
import pytest

from app.parsers.checkpoint import checkpoint_progress
from app.parsers.pymupdf_parser import PyMuPDFParser


def _save_page_sequence(path, page_count: int = 5) -> None:
    document = fitz.open()
    document.set_metadata({"title": "Checkpoint Test Paper"})
    for page_number in range(1, page_count + 1):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {page_number} checkpoint content", fontsize=12)
    document.save(path)
    document.close()


class RecordingParser(PyMuPDFParser):
    def __init__(self, fail_on_page: int | None = None) -> None:
        self.fail_on_page = fail_on_page
        self.visited_pages: list[int] = []

    def _get_textpage(self, page, page_number):
        self.visited_pages.append(page_number)
        if page_number == self.fail_on_page:
            raise OSError(f"simulated page {page_number} failure")
        return super()._get_textpage(page, page_number)


def test_resumes_from_last_complete_page_batch(tmp_path) -> None:
    source = tmp_path / "paper.pdf"
    checkpoint_dir = tmp_path / "checkpoint"
    _save_page_sequence(source)

    first_progress: list[tuple[int, int]] = []
    first = RecordingParser(fail_on_page=4)
    with pytest.raises(OSError, match="simulated page 4 failure"):
        first.parse(
            str(source),
            checkpoint_dir=checkpoint_dir,
            batch_size=2,
            source_fingerprint="fixture-sha256",
            progress_callback=lambda complete, total: first_progress.append((complete, total)),
        )

    assert first.visited_pages == [1, 2, 3, 4]
    assert first_progress == [(2, 5)]
    assert checkpoint_progress(checkpoint_dir) == {
        "completed_pages": 2,
        "page_count": 5,
        "percentage": 40.0,
        "resumable": True,
        "updated_at": json.loads(
            (checkpoint_dir / "manifest.json").read_text(encoding="utf-8")
        )["updated_at"],
    }

    resumed_progress: list[tuple[int, int]] = []
    resumed = RecordingParser()
    parsed = resumed.parse(
        str(source),
        checkpoint_dir=checkpoint_dir,
        batch_size=2,
        source_fingerprint="fixture-sha256",
        progress_callback=lambda complete, total: resumed_progress.append((complete, total)),
    )

    assert resumed.visited_pages == [3, 4, 5]
    assert resumed_progress == [(4, 5), (5, 5)]
    assert [page.page_number for page in parsed.pages] == [1, 2, 3, 4, 5]
    assert "Page 1 checkpoint content" in parsed.full_text
    assert "Page 5 checkpoint content" in parsed.full_text
    assert checkpoint_progress(checkpoint_dir)["percentage"] == 100.0
    assert checkpoint_progress(checkpoint_dir)["resumable"] is True
    assert not list(checkpoint_dir.glob("*.part"))

    direct = PyMuPDFParser().parse(str(source))
    assert parsed.to_dict() == direct.to_dict()


def test_invalid_checkpoint_is_discarded_and_rebuilt(tmp_path) -> None:
    source = tmp_path / "paper.pdf"
    checkpoint_dir = tmp_path / "checkpoint"
    _save_page_sequence(source, page_count=3)

    PyMuPDFParser().parse(
        str(source),
        checkpoint_dir=checkpoint_dir,
        batch_size=2,
        source_fingerprint="old-source",
    )

    parser = RecordingParser()
    parsed = parser.parse(
        str(source),
        checkpoint_dir=checkpoint_dir,
        batch_size=2,
        source_fingerprint="new-source",
    )

    assert parser.visited_pages == [1, 2, 3]
    assert len(parsed.pages) == 3
    manifest = json.loads((checkpoint_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_fingerprint"] == "new-source"
    assert manifest["completed_pages"] == 3
