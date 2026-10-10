import hashlib
import json
from types import SimpleNamespace

import pytest

from app.llm.base import GeneratedAnswer, LLMResponseError
from scripts.evaluate_official_first_round import OneAttemptProvider, select_cases
from scripts.review_official_first_round import check_reviewed_fact, review


@pytest.mark.asyncio
async def test_first_round_never_retries_generation():
    class Delegate:
        name = "deepseek"
        model = "test-model"
        supports_translation = True
        calls = 0

        async def generate_grounded_answer(self, question, evidence):
            self.calls += 1
            return GeneratedAnswer(answer="no evidence", claims=[])

    delegate = Delegate()
    provider = OneAttemptProvider(delegate)
    await provider.generate_grounded_answer("question", [])
    with pytest.raises(LLMResponseError, match="prohibits automatic retries"):
        await provider.generate_grounded_answer("question", [])
    assert provider.calls == delegate.calls == 1


def response_with_claim(text, quote):
    return SimpleNamespace(
        claims=[SimpleNamespace(text=text, evidence_ids=["E1"])],
        evidence=[SimpleNamespace(evidence_id="E1", document_id="doc", page_number=4,
                                  quote=quote)],
    )


def test_gpt2_old_context_is_not_an_answer_about_gpt1():
    fact = {
        "source_filename": "gpt2.pdf", "source_pages": [4],
        "source_text_contains_any": ["context size from 512 to 1024"],
        "claim_contains_any": ["512"], "claim_contains_any_groups": [["GPT-1", "GPT1"]],
    }
    response = response_with_claim("GPT-2 increased context from 512 to 1024",
                                   "context size from 512 to 1024")
    assert not check_reviewed_fact(fact, response, {"gpt2.pdf": "doc"})
    response.claims[0].text = "GPT-1: 512; GPT-2: 1024, according to GPT-2's context comparison"
    assert check_reviewed_fact(fact, response, {"gpt2.pdf": "doc"})


def test_derived_difference_requires_both_operands_in_the_same_cited_source():
    fact = {
        "source_filename": "table.pdf", "source_pages": [4],
        "source_text_contains_any": ["26.36"], "source_text_contains_all": ["28.4", "26.36"],
        "claim_contains_any": ["2.04"],
        "derivation": {"left": "28.4", "right": "26.36", "result": "2.04"},
    }
    response = response_with_claim("The derived improvement is 2.04", "Previous best: 26.36")
    assert not check_reviewed_fact(fact, response, {"table.pdf": "doc"})
    response.evidence[0].quote = "Transformer: 28.4; previous best: 26.36"
    assert check_reviewed_fact(fact, response, {"table.pdf": "doc"})
    fact["derivation"]["result"] = "3.24"
    assert not check_reviewed_fact(fact, response, {"table.pdf": "doc"})


@pytest.mark.asyncio
async def test_failed_attempt_is_counted_and_not_retried():
    class Delegate:
        name = "deepseek"
        model = "test-model"
        supports_translation = True

        async def generate_grounded_answer(self, question, evidence):
            raise LLMResponseError("bad response")

    provider = OneAttemptProvider(Delegate())
    with pytest.raises(LLMResponseError, match="bad response"):
        await provider.generate_grounded_answer("question", [])
    assert provider.calls == 1
    assert provider.raw is None
    with pytest.raises(LLMResponseError, match="prohibits automatic retries"):
        await provider.generate_grounded_answer("question", [])


def offline_inputs(tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"cases": [{"case_id": "one"}]}), encoding="utf-8")
    report = tmp_path / "original.json"
    report.write_text(json.dumps({
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "model_calls": 9, "documents": [],
        "cases": [{"case_id": "one", "failures": ["no_successful_model_answer"]}],
    }), encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "policy_id": "test", "fact_overrides": {},
        "source_inspection": {"reviewer": "test", "scope": "test",
                              "cases": {"one": {"passed": False, "reason": "no answer"}}},
    }), encoding="utf-8")
    return report, dataset, policy


def test_offline_review_preserves_original_and_refuses_to_overwrite(tmp_path):
    report, dataset, policy = offline_inputs(tmp_path)
    original_bytes = report.read_bytes()
    output = tmp_path / "review.json"
    result = review(report, dataset, policy, output)
    assert result["model_calls"] == 0
    assert result["original_model_calls"] == 9
    assert result["status"] == "failed"
    assert report.read_bytes() == original_bytes
    assert result["original_report_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    output_bytes = output.read_bytes()
    with pytest.raises(ValueError, match="must not overwrite"):
        review(report, dataset, policy, output)
    assert output.read_bytes() == output_bytes


def test_offline_review_rejects_changed_original_gold(tmp_path):
    report, dataset, policy = offline_inputs(tmp_path)
    dataset.write_text('{"cases": []}', encoding="utf-8")
    output = tmp_path / "review.json"
    with pytest.raises(ValueError, match="dataset SHA differs"):
        review(report, dataset, policy, output)
    assert not output.exists()


def test_selected_retest_preserves_original_cases_and_budget():
    dataset = {"cases": [{"case_id": "a", "question": "original A"},
                         {"case_id": "b", "question": "original B"}]}
    selected = select_cases(dataset, ["b"], 1)
    assert selected == [dataset["cases"][1]]
    assert selected[0] is dataset["cases"][1]
    assert len(dataset["cases"]) == 2
    assert select_cases(dataset, None, 2) == dataset["cases"]


@pytest.mark.parametrize("case_ids,budget", [(["a", "b"], 1), (["a"], 0),
                                          (["a"], 10), (["unknown"], 1),
                                          (["a", "a"], 2), ([], 1)])
def test_retest_rejects_invalid_selection_before_cloud_call(case_ids, budget):
    with pytest.raises(ValueError):
        select_cases({"cases": [{"case_id": "a"}, {"case_id": "b"}]}, case_ids, budget)


def test_offline_review_does_not_inflate_subset_into_whole_dataset(tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"cases": [{"case_id": "a"}, {"case_id": "b"}]}))
    report = tmp_path / "report.json"
    dataset_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
    report.write_text(json.dumps({"dataset_sha256": dataset_sha,
                                  "selected_case_ids": ["b"], "model_calls": 1,
                                  "documents": [], "cases": [{"case_id": "b", "failures": []}]}))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"policy_id": "subset", "fact_overrides": {},
                                  "source_inspection": {"reviewer": "test", "scope": "subset",
                                  "cases": {"b": {"passed": False, "reason": "no response"}}}}))
    outcome = review(report, dataset, policy, tmp_path / "review.json")
    assert outcome["original_dataset_case_count"] == 2
    assert outcome["summary"]["total"] == 1
    assert outcome["summary"]["accepted"] == 0
    assert outcome["selected_case_ids"] == ["b"]
