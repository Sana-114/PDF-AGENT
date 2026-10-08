from copy import deepcopy

import pytest

from scripts.summarize_comparison_stability import _rate, summarize


def _dataset() -> dict:
    return {
        "dataset_id": "comparison-stability-test",
        "expected_provider": "deepseek",
        "required_retrieval_mode": "reranked",
        "stability_thresholds": {
            "min_round_pass_rate": 1.0,
            "min_case_pass_rate": 1.0,
            "min_fact_pass_rate": 1.0,
            "max_fact_omission_rate": 0.0,
            "min_refusal_rate": 1.0,
        },
        "cases": [
            {
                "case_id": "supported-comparison",
                "required_claim_facts": [{"fact_id": "fact-a"}],
                "required_cross_document_facts": [{"fact_id": "two-source-fact"}],
            },
            {
                "case_id": "unsupported-comparison",
                "must_refuse": True,
                "required_claim_facts": [],
            },
        ],
    }


def _report() -> dict:
    return {
        "dataset_id": "comparison-stability-test",
        "status": "passed",
        "provider": "deepseek",
        "model": "deepseek-flash",
        "required_retrieval_mode": "reranked",
        "cases": [
            {
                "case_id": "supported-comparison",
                "passed": True,
                "failures": [],
                "facts": {"fact-a": True, "two-source-fact": True},
                "document_ids": {"a.pdf": "document-a", "b.pdf": "document-b"},
                "latency_ms": 100,
                "response": {
                    "claims": [{"text": "comparison", "evidence_ids": ["E1", "E2"]}],
                    "evidence": [
                        {
                            "document_id": "document-a",
                            "page_number": 2,
                            "block_ids": ["p2-b1"],
                        },
                        {
                            "document_id": "document-b",
                            "page_number": 4,
                            "block_ids": ["p4-b1"],
                        },
                    ],
                    "audit": {"cross_document_claim_count": 1},
                    "insufficient_evidence": False,
                },
            },
            {
                "case_id": "unsupported-comparison",
                "passed": True,
                "failures": [],
                "facts": {},
                "document_ids": {"a.pdf": "document-a", "b.pdf": "document-b"},
                "latency_ms": 50,
                "response": {
                    "claims": [],
                    "evidence": [],
                    "audit": {"cross_document_claim_count": 0},
                    "insufficient_evidence": True,
                },
            },
        ],
    }


def test_stability_summary_passes_repeatable_grounded_reports() -> None:
    first = _report()
    second = deepcopy(first)
    second["cases"][0]["latency_ms"] = 140

    summary = summarize(_dataset(), [first, second], ["round-1.json", "round-2.json"])

    assert summary["status"] == "passed"
    assert summary["metrics"] == {
        "round_pass_rate": 1.0,
        "case_pass_rate": 1.0,
        "fact_pass_rate": 1.0,
        "fact_omission_rate": 0.0,
        "refusal_rate": 1.0,
    }
    assert summary["counts"]["fact_observations"] == 4
    assert summary["cases"][0]["evidence_pairwise_jaccard"] == 1.0


def test_stability_summary_rejects_one_round_fact_omission() -> None:
    first = _report()
    second = deepcopy(first)
    second["status"] = "failed"
    second["cases"][0]["passed"] = False
    second["cases"][0]["facts"]["two-source-fact"] = False
    second["cases"][0]["failures"] = ["missing_fact:two-source-fact"]

    summary = summarize(_dataset(), [first, second])

    assert summary["status"] == "failed"
    assert summary["metrics"]["fact_omission_rate"] == 0.25
    assert "threshold:fact_pass_rate" in summary["failures"]
    assert "threshold:fact_omission_rate" in summary["failures"]


def test_stability_summary_rejects_metadata_mismatch() -> None:
    report = _report()
    report["provider"] = "mock"

    summary = summarize(_dataset(), [report])

    assert summary["status"] == "failed"
    assert "round_1:metadata_mismatch" in summary["failures"]


def test_missing_cases_reduce_rates_instead_of_disappearing_from_denominators() -> None:
    first = _report()
    second = _report()
    second["cases"] = []

    summary = summarize(_dataset(), [first, second])

    assert summary["status"] == "failed"
    assert summary["metrics"]["case_pass_rate"] == 0.5
    assert summary["metrics"]["fact_pass_rate"] == 0.5
    assert summary["metrics"]["refusal_rate"] == 0.5
    assert summary["counts"]["case_observations"] == 4
    assert summary["cases"][0]["rounds_observed"] == 1
    assert summary["cases"][0]["rounds_expected"] == 2


def test_refusal_rate_does_not_count_failed_publication_as_safe_refusal() -> None:
    report = _report()
    report["cases"][1]["passed"] = False
    report["cases"][1]["failures"] = ["invented_target_quantity"]

    summary = summarize(_dataset(), [report])

    assert summary["metrics"]["refusal_rate"] == 0.0
    assert summary["status"] == "failed"


@pytest.mark.parametrize("change", ["duplicate", "model"])
def test_stability_summary_rejects_duplicate_cases_or_model_changes(change: str) -> None:
    first = _report()
    second = _report()
    if change == "duplicate":
        second["cases"].append(deepcopy(second["cases"][0]))
    else:
        second["model"] = "different-model"

    summary = summarize(_dataset(), [first, second])

    assert summary["status"] == "failed"
    assert (
        f"round_2:{'duplicate_case' if change == 'duplicate' else 'model_mismatch'}"
        in (summary["failures"])
    )


def test_release_rate_does_not_round_one_miss_up_to_perfect() -> None:
    assert _rate(19999, 20000) < 1.0


def test_dataset_without_required_facts_has_zero_omission_rate() -> None:
    dataset = _dataset()
    dataset["cases"] = [dataset["cases"][1]]
    report = _report()
    report["cases"] = [report["cases"][1]]

    summary = summarize(dataset, [report])

    assert summary["status"] == "passed"
    assert summary["metrics"]["fact_omission_rate"] == 0.0
