import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.llm.base import GeneratedAnswer, LLMResponseError
from scripts.evaluate_official_first_round import (
    OneAttemptProvider,
    ReadOnlyOfficialIndex,
    restore_saved_documents,
    select_cases,
    validate_saved_index,
)
from scripts.review_official_first_round import check_reviewed_fact, review


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


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


@pytest.mark.parametrize("matching", [True, False])
def test_offline_source_inspection_is_bound_to_its_generation_report(tmp_path, matching):
    report, dataset, policy = offline_inputs(tmp_path)
    inspection = json.loads(policy.read_text(encoding="utf-8"))
    inspection["original_report_sha256"] = (
        hashlib.sha256(report.read_bytes()).hexdigest() if matching else "0" * 64)
    policy.write_text(json.dumps(inspection), encoding="utf-8")
    output = tmp_path / "bound-review.json"
    if matching:
        assert review(report, dataset, policy, output)["model_calls"] == 0
    else:
        with pytest.raises(ValueError, match="bound to a different report"):
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


def test_saved_generation_index_scope_and_identity_are_validated_before_cloud_calls():
    entry = {"filename": "public.pdf", "sha256": "public-hash", "origin": "official"}
    dataset = {"files": [entry]}
    previous = {
        "evaluation_kind": "official_original_questions_production_ask_harness",
        "production_library_modified": False, "dataset_sha256": "dataset-hash",
        "temporary_vector_collection": "official_first_round_" + "a" * 32,
        "documents": [{**entry, "id": "document", "chunks": 1, "pages": 1}],
    }
    assert validate_saved_index(previous, dataset, "dataset-hash").startswith("official_first")
    for change in ({"dataset_sha256": "changed"}, {"production_library_modified": True},
                   {"temporary_vector_collection": "production"}, {"documents": []},
                   {"documents": previous["documents"] * 2},
                   {"documents": [{**previous["documents"][0], "sha256": "changed"}]}):
        with pytest.raises(ValueError):
            validate_saved_index(previous | change, dataset, "dataset-hash")


@pytest.mark.asyncio
async def test_full_chain_gate_blocks_cloud_calls_on_retrieval_fallback():
    class Delegate:
        name = "deepseek"
        model = "test"
        supports_translation = True

        async def generate_grounded_answer(self, question, evidence):
            pytest.fail("Degraded evidence may not trigger a paid cloud call")

    provider = OneAttemptProvider(Delegate(), require_full_retrieval=True)
    with pytest.raises(LLMResponseError, match="requires real hybrid"):
        await provider.generate_grounded_answer("question", [])
    with pytest.raises(LLMResponseError, match="requires real hybrid"):
        await provider.generate_grounded_answer("question", [SimpleNamespace(
            recall_mode="lexical", retrieval_mode="reranked")])
    assert provider.calls == 0 and provider.request is None


def test_saved_payload_restoration_rejects_wrong_source_page_id_and_count(db_session):
    record = {"id": "doc", "filename": "public.pdf", "sha256": "sha",
              "title": "Public", "chunks": 1, "pages": 1}
    payload = {"document_id": "doc", "chunk_id": "chunk", "page_number": 1,
               "block_ids": ["p1-b1"], "bbox": [1, 2, 3, 4], "text": "Public quote"}
    class Client:
        def scroll(self, **kwargs):
            assert kwargs["with_vectors"] is False
            return [SimpleNamespace(id="chunk", payload=self.payload)], None

    client = Client()
    index = SimpleNamespace(client=client, collection_name="isolated")
    for change in ({"document_id": "private"}, {"page_number": 2}, {"chunk_id": "changed"}):
        client.payload = payload | change
        with pytest.raises(ValueError, match="Invalid or out-of-scope"):
            restore_saved_documents(db_session, index, [record])
        db_session.rollback()
    client.payload = payload
    with pytest.raises(ValueError, match="count mismatch"):
        restore_saved_documents(db_session, index, [{**record, "chunks": 2}])
    db_session.rollback()
    assert restore_saved_documents(db_session, index, [record]) == {"public.pdf": "doc"}


def test_saved_index_forbids_writes_recreation_and_automatic_backfill(db_session):
    index = ReadOnlyOfficialIndex.__new__(ReadOnlyOfficialIndex)
    with pytest.raises(ValueError, match="forbids indexing"):
        index.index_document(db_session, "doc")
    with pytest.raises(ValueError, match="forbids deletion"):
        index.delete_document("doc")
    index._collection_ready = False
    index.collection_name = "isolated"
    index.client = SimpleNamespace(collection_exists=lambda name: False)
    with pytest.raises(ValueError, match="never recreate"):
        index.ensure_collection()
