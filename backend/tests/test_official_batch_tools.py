import hashlib
import json
import subprocess
from types import SimpleNamespace

import fitz
import pytest

from app.parsers.base import Block, Page, ParsedDocument, ReferenceNode
from scripts.evaluate_official_batch import load_selection, run_batch, summarize
from scripts.evaluate_pdf_corpus import _validate_structure, evaluate_pdf
from scripts.prepare_official_remaining import prepare
from scripts.review_official_batch import review


def make_manifest(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "paper.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "A source paragraph for regression testing.")
        document.save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"downloads": [{
        "filename": source.name, "sha256": digest, "expect": {"exact_pages": 1},
    }]}), encoding="utf-8")
    return corpus, manifest


def test_batch_selection_requires_explicit_checks_and_unchanged_inputs(tmp_path):
    corpus, manifest = make_manifest(tmp_path)
    assert len(load_selection(corpus, manifest)) == 1
    payload = json.loads(manifest.read_text())
    payload["downloads"][0]["expect"] = {}
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="missing acceptance"):
        load_selection(corpus, manifest)
    payload["downloads"][0]["expect"] = {"exact_pages": 1}
    payload["downloads"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_selection(corpus, manifest)


@pytest.mark.parametrize("names", [[], ["paper.pdf", "paper.pdf"], ["missing.pdf"]])
def test_batch_selection_rejects_invalid_names(tmp_path, names):
    corpus, manifest = make_manifest(tmp_path)
    with pytest.raises(ValueError, match="selection"):
        load_selection(corpus, manifest, names)


def test_batch_failure_continues_without_cloud_or_retry(tmp_path, monkeypatch):
    corpus, manifest = make_manifest(tmp_path)
    seen = []

    def fail(command, **kwargs):
        seen.append(command)
        assert kwargs["env"]["LLM_API_KEY"] == ""
        assert kwargs["env"]["LLM_PROVIDER"] == "mock"
        assert kwargs["env"]["VECTOR_SEARCH_ENABLED"] == "false"
        return SimpleNamespace(returncode=2, stdout=b"", stderr=b"parser error")

    monkeypatch.setattr("scripts.evaluate_official_batch.subprocess.run", fail)
    report = run_batch(corpus, manifest, tmp_path / "results")
    assert len(seen) == 1
    assert report["summary"]["errors"] == 1
    assert report["summary"]["parsed_complete"] == 0
    assert report["model_calls"] == report["automatic_retries"] == 0
    assert (tmp_path / "results/batch-1.json").exists()


def test_batch_timeout_preserves_failure_and_does_not_retry(tmp_path, monkeypatch):
    corpus, manifest = make_manifest(tmp_path)
    calls = []

    def timeout(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"started")

    monkeypatch.setattr("scripts.evaluate_official_batch.subprocess.run", timeout)
    report = run_batch(corpus, manifest, tmp_path / "results", timeout_seconds=.01)
    assert len(calls) == 1
    assert report["summary"]["timeouts"] == 1
    assert report["summary"]["scoped_checks_passed"] == 0


def test_batch_never_overwrites_an_existing_report_directory(tmp_path):
    corpus, manifest = make_manifest(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    with pytest.raises(FileExistsError):
        run_batch(corpus, manifest, results)


def test_scoped_passes_are_separate_from_parse_completion():
    summary = summarize([
        {"status": "failed", "page_count": 10, "parsed_page_count": 10},
        {"status": "timeout"}, {"status": "not_run"},
    ])
    assert summary["parsed_complete"] == 1
    assert summary["scoped_checks_passed"] == 0
    assert summary["not_run"] == 1


def test_corpus_artifacts_preserve_ast_and_checkpoints(tmp_path):
    corpus, _ = make_manifest(tmp_path)
    artifacts = tmp_path / "artifacts"
    result = evaluate_pdf(corpus / "paper.pdf", {"exact_pages": 1}, artifacts_dir=artifacts)
    assert result["status"] == "passed"
    assert result["parsed_page_count"] == 1
    assert (artifacts / "parsed.json").exists()
    assert (artifacts / "checkpoints/manifest.json").exists()
    with pytest.raises(FileExistsError):
        evaluate_pdf(corpus / "paper.pdf", artifacts_dir=artifacts)


def test_reference_probe_must_match_correct_page_and_reference_not_body():
    page = Page(1, 600, 800, [Block("p1-b1", "text", "Author. 2020. Title.",
                                  [1, 2, 100, 20], 0)])
    parsed = ParsedDocument("0.2", None, [], [page], page.blocks[0].text)
    sample = {"reference_samples": [{"page_number": 1, "contains": "2020. Title."}]}
    assert "missing source reference" in _validate_structure(parsed, sample)[0]
    parsed.references.append(ReferenceNode("r1", "1", page.blocks[0].text,
                                           1, [1, 2, 100, 20], ["p1-b1"]))
    assert _validate_structure(parsed, sample) == []
    sample["reference_samples"][0]["page_number"] = 2
    assert "missing source reference" in _validate_structure(parsed, sample)[0]


def test_numbered_reference_probe_checks_separate_label_and_text():
    page = Page(1, 600, 800, [Block("p1-b1", "text", "[1] Author. 2020. Title.",
                                  [1, 2, 100, 20], 0)])
    parsed = ParsedDocument("0.2", None, [], [page], page.blocks[0].text, references=[
        ReferenceNode("r1", "1", "Author. 2020. Title.", 1, [1, 2, 100, 20], ["p1-b1"]),
    ])
    sample = {"reference_samples": [{"page_number": 1, "contains": "[1] Author. 2020. Title."}]}
    assert _validate_structure(parsed, sample) == []
    parsed.references[0].label = "2"
    assert "missing source reference" in _validate_structure(parsed, sample)[0]
    parsed.references[0].label = "1"
    parsed.references[0].text = "Author. 2021. Wrong title."
    assert "missing source reference" in _validate_structure(parsed, sample)[0]


def test_preparation_uses_native_source_not_parsed_outputs(tmp_path):
    corpus, _ = make_manifest(tmp_path)
    source = corpus / "paper.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Independent Paper Title\nAlice Example\nAbstract\nSource text.")
        document.save(source)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"documents": [{
        "filename": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "pages": 1, "native_text_characters": 70,
    }]}))
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"excluded_previously_tested": [], "documents": [{
        "filename": source.name, "title_contains": "Independent Paper Title",
        "author": "Alice Example",
    }]}))
    result = prepare(corpus, inventory, metadata, tmp_path / "prepared")
    assert result["documents"] == 1
    assert result["model_calls"] == 0
    checks = json.loads((tmp_path / "prepared/manifest.json").read_text())["downloads"][0]
    assert checks["expect"]["required_authors"] == ["Alice Example"]
    assert checks["expect"]["min_references"] == 1
    # Missing bibliography is not silently turned into a zero-reference success criterion.
    assert "reference_samples" not in checks["expect"]
    payload = json.loads(inventory.read_text())
    payload["documents"][0]["sha256"] = "0" * 64
    inventory.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        prepare(corpus, inventory, metadata, tmp_path / "changed-source")
    assert not (tmp_path / "changed-source").exists()


def test_saved_ast_review_preserves_original_report_and_checks_manifest(tmp_path):
    corpus, manifest = make_manifest(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    job = results / "document-01"
    job.mkdir()
    parsed_result = evaluate_pdf(corpus / "paper.pdf", {"exact_pages": 1},
                                 artifacts_dir=job / "artifacts")
    parsed_result["artifacts_directory"] = job.name
    source_report = results / "report.json"
    source_report.write_text(json.dumps({
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "summary": summarize([parsed_result]), "documents": [parsed_result],
        "acceptance_scope": "unit fixture scoped checks",
    }))
    original_bytes = source_report.read_bytes()
    result = review(source_report, manifest, results / "reviewed.json")
    assert result["summary"]["scoped_checks_passed"] == 1
    assert result["parser_calls"] == result["model_calls"] == 0
    assert source_report.read_bytes() == original_bytes
    with pytest.raises(FileExistsError):
        review(source_report, manifest, results / "reviewed.json")
    payload = json.loads(manifest.read_text())
    payload["downloads"][0]["expect"]["exact_pages"] = 2
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Manifest changed"):
        review(source_report, manifest, results / "changed.json")
