import copy

import pytest

from scripts.diagnose_public_counterexample import verified_inputs


def inputs():
    policy = {"source_sha256": "source-sha", "cases": [{"case_id": "fixed", "question": "fixed?"}]}
    snapshot = {"document": {"id": "public", "sha256": "source-sha", "title": "Public"},
                "chunks": [{"id": "chunk", "document_id": "public", "text": "source text",
                            "page_number": 1, "block_ids_json": '["p1-b1"]', "section": None}]}
    report = {"source_sha256": "source-sha", "dataset_sha256": "dataset-sha", "document_id": "public",
              "cases": [{"case_id": "fixed", "question": "fixed?", "response": {"evidence": [
                  {"evidence_id": "E1", "chunk_id": "chunk", "document_id": "public",
                   "document_title": "Public", "page_number": 1, "quote": "source text",
                   "block_ids": ["p1-b1"], "score": 1, "recall_mode": "hybrid",
                   "retrieval_mode": "reranked"}]}}]}
    return policy, report, snapshot


def test_exact_snapshot_only_is_accepted():
    policy, report, snapshot = inputs()
    case, evidence = verified_inputs(policy, report, snapshot, "source-sha", "fixed", "dataset-sha")
    assert case["question"] == "fixed?" and evidence[0].quote == "source text"


@pytest.mark.parametrize("field,value", [
    ("quote", "injected"), ("page_number", 2), ("block_ids", ["p2-b1"]),
    ("document_id", "private"), ("document_title", "Different"), ("recall_mode", "lexical"),
    ("retrieval_mode", "hybrid"), ("section", "Different"),
])
def test_changed_or_out_of_scope_anchor_is_rejected_before_cloud(field, value):
    policy, report, snapshot = inputs()
    report["cases"][0]["response"]["evidence"][0][field] = value
    with pytest.raises(ValueError):
        verified_inputs(policy, report, snapshot, "source-sha", "fixed", "dataset-sha")


@pytest.mark.parametrize("change", ["dataset", "source", "question", "duplicate"])
def test_identity_and_original_question_changes_are_rejected(change):
    policy, report, snapshot = inputs()
    if change == "dataset":
        report["dataset_sha256"] = "other"
    elif change == "source":
        snapshot["document"]["sha256"] = "other"
    elif change == "question":
        report["cases"][0]["question"] = "easier?"
    else:
        report["cases"].append(copy.deepcopy(report["cases"][0]))
    with pytest.raises(ValueError):
        verified_inputs(policy, report, snapshot, "source-sha", "fixed", "dataset-sha")
