import copy

import pytest

from scripts.combine_official_disjoint_retests import app_fingerprint, combine


def fixture():
    original = {
        "evaluation_kind": "official_all_39_one_sample_host_generation",
        "dataset_sha256": "gold",
        "source_gold_sha256": "source",
        "cases": [],
        "documents": [],
    }
    for n in range(39):
        name = f"{n}.pdf"
        original["cases"].append({"filename": name, "question": f"Original {n}?"})
        original["documents"].append(
            {
                "filename": name,
                "sha256": f"pdf{n}",
                "ast_sha256": f"ast{n}",
                "id": str(n),
                "pages": 1,
                "collection": f"isolated{n}",
            }
        )
    parts = []
    for start, end in ((0, 7), (7, 8), (8, 39)):
        part = {
            "evaluation_kind": "official_unchanged_corpus_questions_readonly_generation_retest",
            "status": "completed_pending_source_review",
            "production_library_modified": False,
            "vector_index_modified": False,
            "automatic_retries": 0,
            "provider": "deepseek",
            "original_report_sha256": "base",
            "dataset_sha256": "gold",
            "source_gold_sha256": "source",
            "runtime_code_sha256": {"app/example.py": "b" * 64},
            "documents": copy.deepcopy(original["documents"][start:end]),
            "cases": [
                {
                    **row,
                    "model_calls": 1,
                    "status": "machine_passed_pending_source_review",
                    "response": {"provider": "deepseek"},
                    "raw_provider_output": {"claim": "saved"},
                }
                for row in original["cases"][start:end]
            ],
            "model_calls": end - start,
        }
        parts.append((f"part{start}.json", str(start), part))
    return original, parts, app_fingerprint(parts[0][2])


def test_preserves_failed_attempt_and_does_not_generate():
    original, parts, fingerprint = fixture()
    row = parts[0][2]["cases"][0]
    row.update(
        status="failed",
        failures=["timeout"],
        response={"provider": "extractive"},
        raw_provider_output=None,
    )
    result = combine(original, "base", parts, fingerprint)
    assert result["summary"] == {"files_attempted": 39, "machine_passed": 38, "failed": 1}
    assert result["new_cloud_calls"] == 0 and result["single_continuous_generation_run"] is False
    assert result["cases"][0]["failures"] == ["timeout"]
    assert "component_report_sha256" not in row


@pytest.mark.parametrize(
    "field,value", [("question", "Rephrased?"), ("model_calls", 2), ("status", "running")]
)
def test_changed_or_repeated_attempt_rejected(field, value):
    original, parts, fingerprint = fixture()
    parts[0][2]["cases"][0][field] = value
    with pytest.raises(ValueError):
        combine(original, "base", parts, fingerprint)


@pytest.mark.parametrize("field", ["sha256", "ast_sha256", "id", "pages", "collection"])
def test_source_drift_rejected(field):
    original, parts, fingerprint = fixture()
    parts[0][2]["documents"][0][field] = "changed"
    with pytest.raises(ValueError):
        combine(original, "base", parts, fingerprint)


def test_duplicate_executed_question_rejected():
    original, parts, fingerprint = fixture()
    with pytest.raises(ValueError, match="disjoint"):
        combine(original, "base", parts + [parts[0]], fingerprint)


def test_incomplete_coverage_cannot_use_prior_success():
    original, parts, fingerprint = fixture()
    with pytest.raises(ValueError, match="Incomplete"):
        combine(original, "base", parts[:-1], fingerprint)


def test_mixed_candidate_rejected():
    original, parts, fingerprint = fixture()
    parts[1][2]["runtime_code_sha256"]["app/example.py"] = "c" * 64
    with pytest.raises(ValueError, match="same-app"):
        combine(original, "base", parts, fingerprint)


def test_fallback_cannot_be_counted_as_real_generation_success():
    original, parts, fingerprint = fixture()
    parts[1][2]["cases"][0]["response"]["provider"] = "extractive"
    with pytest.raises(ValueError, match="Fallback"):
        combine(original, "base", parts, fingerprint)
