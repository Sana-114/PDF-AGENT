from types import SimpleNamespace

from scripts.inventory_official_pdfs import inventory


def test_inventory_counts_native_and_image_pages_without_parsing(tmp_path, monkeypatch) -> None:
    (tmp_path / "sample.pdf").write_bytes(b"fixture bytes, not a PDF")

    class FakePdf:
        def __enter__(self):
            return [
                SimpleNamespace(get_text=lambda mode, text=text: text)
                for text in ["hello", "", " "]
            ]

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("scripts.inventory_official_pdfs.fitz.open", lambda path: FakePdf())
    report = inventory(tmp_path)
    assert report["kind"] == "file_inventory_not_parser_acceptance"
    assert report["status"] == "passed"
    assert report["summary"]["pdf_files"] == 1
    assert report["summary"]["pages"] == 3
    assert report["documents"][0]["pages_without_native_text"] == 2
    assert report["documents"][0]["native_text_characters"] == 5
    assert len(report["documents"][0]["sha256"]) == 64


def test_inventory_records_unreadable_inputs_instead_of_dropping_them(tmp_path, monkeypatch):
    (tmp_path / "broken.pdf").write_bytes(b"broken fixture")

    def fail(path):
        raise ValueError("invalid PDF")

    monkeypatch.setattr("scripts.inventory_official_pdfs.fitz.open", fail)
    report = inventory(tmp_path)
    assert report["status"] == "failed"
    assert report["summary"]["pdf_files"] == 1
    assert report["documents"][0]["error"] == "ValueError: invalid PDF"


def test_empty_inventory_cannot_pass(tmp_path):
    assert inventory(tmp_path)["status"] == "failed"
