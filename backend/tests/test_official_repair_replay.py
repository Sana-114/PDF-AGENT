import hashlib
import json

import pytest

from scripts.replay_official_failure_repairs import AVAILABLE_CASES, TARGETS, replay


@pytest.mark.asyncio
async def test_replay_never_overwrites_a_preserved_report(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("original result", encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        await replay(tmp_path / "absent.json", tmp_path / "absent-dataset.json", output)
    assert output.read_text(encoding="utf-8") == "original result"


@pytest.mark.asyncio
async def test_replay_rejects_changed_gold_before_any_provider_or_retrieval(tmp_path, monkeypatch):
    def forbidden(*_):
        raise AssertionError("offline replay must not instantiate a model")

    monkeypatch.setattr("app.agent.harness.get_llm_provider", forbidden)
    source, dataset, output = (tmp_path / name for name in ("raw.json", "gold.json", "out.json"))
    source.write_text(json.dumps({"dataset_sha256": "0" * 64}), encoding="utf-8")
    dataset.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset SHA"):
        await replay(source, dataset, output)
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_fields, passed", [([], False), (["GPT-1 参数量"], True)])
@pytest.mark.parametrize("selected, rejection", [
    (None, []),
    (["gpt-parameter-evolution", "context-evolution"], []),
    (["gpt-parameter-evolution"], ["ordinal_model_equivalence_mismatch"]),
])
async def test_guard_replay_requires_exact_missing_fields_not_just_a_subset(
    tmp_path, monkeypatch, expected_fields, passed, selected, rejection,
):
    def forbidden(*_):
        raise AssertionError("saved-output replay must never instantiate a cloud model")

    monkeypatch.setattr("app.agent.harness.get_llm_provider", forbidden)
    cases = [{"case_id": key, "documents": ["public.pdf"], "question": "GPT-1 参数量是多少？"}
             for key in TARGETS]
    dataset = tmp_path / "gold.json"
    dataset.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    report = {"dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
              "model": "saved-test", "documents": [{"filename": "public.pdf", "id": "paper"}],
              "cases": [{"case_id": key, "response": {"evidence": [{"evidence_id": "E1",
                  "document_id": "paper", "page_number": 4,
                  "quote": "The model has 80M parameters.", "score": 0.8}]},
                  "raw_generated": {"answer": "", "claims": []}} for key in TARGETS]}
    source = tmp_path / "raw.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    expectations = tmp_path / "expectations.json"
    selected_keys = list(TARGETS) if selected is None else selected
    expectations.write_text(json.dumps({"cases": {key: {
        "insufficient_evidence": True, "missing_fields": expected_fields,
        "required_semantic_rejections": rejection,
    } for key in selected_keys}}), encoding="utf-8")
    result = await replay(source, dataset, tmp_path / "out.json", expectations, selected)
    assert (result["status"] == "passed") == (passed and not rejection)
    assert result["selected_case_ids"] == selected_keys
    assert result["model_calls"] == 0 and not result["is_new_end_to_end_generation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"insufficient_evidence": 1, "missing_fields": []},
    {"insufficient_evidence": "false", "missing_fields": []},
    {"insufficient_evidence": False, "missing_fields": "GPT-1"},
    {"insufficient_evidence": False, "missing_fields": [None]},
    {"insufficient_evidence": False, "missing_fields": [""]},
])
async def test_replay_rejects_malformed_expectations_before_generation(
    tmp_path, monkeypatch, invalid,
):
    def forbidden(*_):
        raise AssertionError("invalid audit policy must not instantiate a model")

    monkeypatch.setattr("app.agent.harness.get_llm_provider", forbidden)
    dataset = tmp_path / "gold.json"
    dataset.write_text(json.dumps({"cases": []}), encoding="utf-8")
    source = tmp_path / "raw.json"
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    source.write_text(json.dumps({"dataset_sha256": digest}), encoding="utf-8")
    expectations = tmp_path / "expectations.json"
    expectations.write_text(json.dumps({"cases": {key: invalid for key in TARGETS}}),
                            encoding="utf-8")
    output = tmp_path / "out.json"
    with pytest.raises(ValueError, match="boolean"):
        await replay(source, dataset, output, expectations)
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [[], ["unknown"], ["context-evolution"] * 2])
async def test_replay_rejects_empty_unknown_or_repeated_selection_before_provider(
    tmp_path, selected,
):
    dataset = tmp_path / "gold.json"
    dataset.write_text(json.dumps({"cases": []}), encoding="utf-8")
    source = tmp_path / "raw.json"
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    source.write_text(json.dumps({"dataset_sha256": digest}), encoding="utf-8")
    output = tmp_path / "out.json"
    with pytest.raises(ValueError, match="selection"):
        await replay(source, dataset, output, case_ids=selected)
    assert not output.exists()


def saved_inputs(tmp_path, cases, quote="Model configuration.", claims=None):
    dataset = tmp_path / "gold.json"
    dataset.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    source = tmp_path / "saved.json"
    source.write_text(json.dumps({
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "model": "saved-synthetic",
        "documents": [{"filename": "public.pdf", "id": "public"}],
        "cases": [{"case_id": case["case_id"], "response": {"evidence": [{
            "evidence_id": "E1", "document_id": "public", "page_number": 1,
            "quote": quote, "score": 1}]}, "raw_generated": {
                "answer": "unchecked", "claims": claims or []}} for case in cases],
    }), encoding="utf-8")
    return source, dataset


@pytest.mark.asyncio
async def test_all_nine_saved_cases_are_replayed_without_a_model_factory_or_retrieval(
    tmp_path, monkeypatch,
):
    def forbidden(*_):
        raise AssertionError("offline audit cannot instantiate models or retrieve")

    monkeypatch.setattr("app.agent.harness.get_llm_provider", forbidden)
    monkeypatch.setattr("app.services.vector_index.get_vector_index", forbidden)
    cases = [{"case_id": key, "documents": ["public.pdf"], "question": "Authors?"}
             for key in AVAILABLE_CASES]
    source, dataset = saved_inputs(tmp_path, cases)
    expectations = tmp_path / "expectations.json"
    expectations.write_text(json.dumps({"cases": {key: {
        "insufficient_evidence": True, "missing_fields": []} for key in AVAILABLE_CASES}}),
        encoding="utf-8")
    result = await replay(source, dataset, tmp_path / "out.json", expectations,
                          list(AVAILABLE_CASES))
    assert result["status"] == "passed" and len(result["cases"]) == 9
    assert result["model_calls"] == 0 and not result["is_new_end_to_end_generation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("addition", [
    {"claim_contains_any": ["a changed expected number"]},
    {"source_filename": "a different paper.pdf"},
    {"claim_contains_any_groups": [None]},
    {"source_text_contains_all": []},
])
async def test_saved_fact_policy_cannot_change_gold_or_use_malformed_groups(tmp_path, addition):
    cases = [{"case_id": "context-evolution", "documents": ["public.pdf"],
              "question": "GPT-1 上下文窗口？", "facts": [{"fact_id": "baseline"}]}]
    source, dataset = saved_inputs(tmp_path, cases)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"fact_overrides": {
        "context-evolution": {"baseline": addition}}}), encoding="utf-8")
    output = tmp_path / "out.json"
    with pytest.raises(ValueError, match="Replay fact policy"):
        await replay(source, dataset, output, case_ids=["context-evolution"],
                     review_policy_path=policy)
    assert not output.exists()


@pytest.mark.asyncio
async def test_saved_review_does_not_borrow_an_earlier_context_value_from_a_later_model(tmp_path):
    fact = {"fact_id": "baseline", "source_filename": "public.pdf", "source_pages": [1],
            "source_text_contains_any": ["640"], "claim_contains_any": ["640"]}
    cases = [{"case_id": "context-evolution", "documents": ["public.pdf"],
              "question": "GPT-1 和 GPT-2 上下文窗口？", "facts": [fact]}]
    source, dataset = saved_inputs(tmp_path, cases, "context size from 640 to 1280 tokens", [{
        "text": "GPT-2 的上下文从640增加到1280 tokens。", "evidence_ids": ["E1"]}])
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"fact_overrides": {"context-evolution": {
        "baseline": {"claim_contains_any_groups": [["GPT-1"]]}}}}), encoding="utf-8")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"cases": {"context-evolution": {
        "insufficient_evidence": True, "missing_fields": ["GPT-1 最大上下文及来源说明"]}}}),
        encoding="utf-8")
    result = await replay(source, dataset, tmp_path / "out.json", expected,
                          ["context-evolution"], policy)
    assert result["status"] == "passed" and not result["cases"][0]["facts"]["baseline"]
    assert result["fact_review_policy_sha256"]
    assert not result["fact_policy_is_scientific_source_review"]


@pytest.mark.asyncio
async def test_replay_uses_the_harness_complete_table_proof_for_bleu_completeness(tmp_path):
    table = """BLEU table
| Model | BLEU | |
| --- | --- | --- |
| | EN-DE | EN-FR |
| Prior | 18 | 26 |
| Example (big) | 20 | 28 |
"""
    cases = [{"case_id": "bleu-improvement", "documents": ["public.pdf"],
              "question": "Example (big) EN-DE BLEU 提升多少？"}]
    source, dataset = saved_inputs(tmp_path, cases, table)
    report = json.loads(source.read_text(encoding="utf-8"))
    report["cases"][0]["response"]["evidence"][0]["source_type"] = "table"
    source.write_text(json.dumps(report), encoding="utf-8")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"cases": {"bleu-improvement": {
        "insufficient_evidence": False, "missing_fields": []}}}), encoding="utf-8")
    result = await replay(source, dataset, tmp_path / "out.json", expected, ["bleu-improvement"])
    assert result["status"] == "passed" and result["cases"][0]["missing_fields"] == []
