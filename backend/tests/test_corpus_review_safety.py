import asyncio
import json

import pytest

from scripts.replay_official_corpus_outputs import replay
from scripts.review_official_corpus_core import validate_policy


def policy_fixture():
    names = [f"document-{n}.pdf" for n in range(39)]
    report = {"cases": [{"filename": name} for name in names]}
    policy = {"original_report_sha256": "a" * 64, "whole_answer_reviewed": False,
              "cases": {name: {"accepted": True, "note": "Bounded manual verdict",
                               "proofs": [[0, "E1", 1]]} for name in names}}
    return report, policy


def test_explicit_complete_bounded_policy_is_valid():
    report, policy = policy_fixture()
    validate_policy(report, policy, "a" * 64)


@pytest.mark.parametrize("change", ["hash", "whole", "missing", "duplicate", "no_proof"])
def test_incompatible_or_unreviewed_policy_never_passes(change):
    report, policy = policy_fixture()
    if change == "hash":
        policy["original_report_sha256"] = "b" * 64
    elif change == "whole":
        policy["whole_answer_reviewed"] = True
    elif change == "missing":
        policy["cases"].pop("document-0.pdf")
    elif change == "duplicate":
        report["cases"][1]["filename"] = "document-0.pdf"
    else:
        policy["cases"]["document-0.pdf"]["proofs"] = []
    with pytest.raises(ValueError):
        validate_policy(report, policy, "a" * 64)


def test_replay_will_not_overwrite_previous_output(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="Never overwrite"):
        asyncio.run(replay(tmp_path / "missing-input", output))
    assert output.read_text() == "original"


def test_replay_incomplete_batch_is_rejected_without_a_provider(tmp_path):
    source = tmp_path / "incomplete.json"
    source.write_text(json.dumps({"model_calls": 1, "cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="complete unchanged isolated"):
        asyncio.run(replay(source, tmp_path / "new.json"))


def test_replay_foreign_document_evidence_is_rejected_before_harness(tmp_path, monkeypatch):
    report = {"evaluation_kind": "official_all_39_one_sample_host_generation",
              "production_library_modified": False, "status": "completed_pending_source_review",
              "model_calls": 39, "cases": [], "documents": [], "model": "cached"}
    for n in range(39):
        name, doc_id = f"document-{n}.pdf", f"doc-{n}"
        report["documents"].append({"filename": name, "id": doc_id})
        report["cases"].append({"filename": name, "raw_generated": {"answer": "x", "claims": []},
            "generation_request": {"evidence": [{"evidence_id": "E1", "document_id": "FOREIGN",
                                                "page_number": 1, "quote": "x", "score": 1}]}})
    def no_agent(*_, **__):
        raise AssertionError("Foreign evidence must be rejected before invoking the Harness")
    monkeypatch.setattr("scripts.replay_official_corpus_outputs.ResearchAgent", no_agent)
    source = tmp_path / "foreign.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="outside the declared paper"):
        asyncio.run(replay(source, tmp_path / "new.json"))
