"""Offline preflight regressions; no provider, API, PDF or Qdrant access."""

import copy

import pytest

from scripts.evaluate_official_corpus_retest import select_originals


def fixtures():
    probes = [
        {
            "filename": f"{i}.pdf",
            "sha256": f"source-{i}",
            "ast_sha256": f"ast-{i}",
            "question": f"question-{i}",
        }
        for i in range(39)
    ]
    previous = {
        "evaluation_kind": "official_all_39_one_sample_host_generation",
        "status": "completed_pending_source_review",
        "production_library_modified": False,
        "documents": [
            {
                **probe,
                "collection": "official_all_corpus_" + "a" * 32 + "_semantic_" + "b" * 10 + "_1024",
            }
            for probe in probes
        ],
        "cases": copy.deepcopy(probes),
    }
    return previous, probes


def test_subset_keeps_original_question_and_selection_order():
    previous, probes = fixtures()
    selected = select_originals(previous, probes, ["8.pdf", "2.pdf"])
    assert [probe["filename"] for probe, _ in selected] == ["8.pdf", "2.pdf"]
    assert selected[0][0] is probes[8]
    assert len(select_originals(previous, probes, None)) == 39


@pytest.mark.parametrize("names", [[], ["1.pdf", "1.pdf"], ["unknown.pdf"]])
def test_invalid_selection_rejected(names):
    with pytest.raises(ValueError):
        select_originals(*fixtures(), names)


@pytest.mark.parametrize("field", ["sha256", "ast_sha256", "question"])
def test_changed_source_ast_or_question_rejected(field):
    previous, probes = fixtures()
    probes[1][field] = "changed"
    with pytest.raises(ValueError):
        select_originals(previous, probes, ["1.pdf"])


def test_production_namespace_rejected():
    previous, probes = fixtures()
    previous["documents"][1]["collection"] = "production_library"
    with pytest.raises(ValueError):
        select_originals(previous, probes, ["1.pdf"])


@pytest.mark.parametrize(
    "field,value", [("status", "running"), ("production_library_modified", True)]
)
def test_untrusted_original_report_rejected(field, value):
    previous, probes = fixtures()
    previous[field] = value
    with pytest.raises(ValueError):
        select_originals(previous, probes, ["1.pdf"])


def test_duplicate_original_document_rejected():
    previous, probes = fixtures()
    previous["documents"][1] = previous["documents"][0]
    with pytest.raises(ValueError):
        select_originals(previous, probes, ["1.pdf"])


def test_pending_continuation_selects_only_the_unexecuted_original_questions():
    previous, probes = fixtures()
    previous.update(status="stopped_provider_failure", cases=previous["cases"][:37])
    selected = select_originals(previous, probes, None, pending_only=True)
    assert [probe["filename"] for probe, _ in selected] == ["37.pdf", "38.pdf"]
    with pytest.raises(ValueError, match="never repeat"):
        select_originals(previous, probes, ["1.pdf"], pending_only=True)
    with pytest.raises(ValueError):
        select_originals(previous, probes, None)


def test_stopped_batch_can_retest_only_an_explicit_executed_subset():
    previous, probes = fixtures()
    previous.update(status="stopped_provider_failure", cases=previous["cases"][:37])
    selected = select_originals(previous, probes, ["3.pdf", "6.pdf"], retest_executed=True)
    assert [probe["filename"] for probe, _ in selected] == ["3.pdf", "6.pdf"]
    for names in (None, ["38.pdf"]):
        with pytest.raises(ValueError, match="Explicitly name"):
            select_originals(previous, probes, names, retest_executed=True)
    with pytest.raises(ValueError, match="Choose pending"):
        select_originals(previous, probes, ["3.pdf"], pending_only=True, retest_executed=True)


@pytest.mark.parametrize("change", ["question", "duplicate", "production", "running"])
def test_pending_continuation_does_not_bypass_source_or_report_safety(change):
    previous, probes = fixtures()
    previous.update(status="stopped_provider_failure", cases=previous["cases"][:37])
    if change == "question":
        previous["cases"][0]["question"] = "changed"
    elif change == "duplicate":
        previous["cases"].append(previous["cases"][0])
    elif change == "production":
        previous["documents"][-1]["collection"] = "production_library"
    else:
        previous["status"] = "running"
    with pytest.raises(ValueError):
        select_originals(previous, probes, None, pending_only=True)
