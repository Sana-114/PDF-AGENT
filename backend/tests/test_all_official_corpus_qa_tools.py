import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.chunking import ChunkDraft, replace_document_chunks
from scripts.evaluate_official_all_document_qa import (
    ReadOnlyOfficialIndex,
    VerifiedAstMismatch,
    check_response,
    index_current_ast,
    normalized,
    prior_call_count,
    source_anchor_valid,
    validate_index_report,
    verify_chunks,
)


def test_fresh_authorized_round_does_not_count_yesterdays_calls():
    assert prior_call_count(None, 39, 39) == 0


@pytest.mark.parametrize("limit", [0, 38])
def test_fresh_round_still_enforces_a_finite_cap(limit):
    with pytest.raises(ValueError, match="batch authorization"):
        prior_call_count(None, 39, limit)


@pytest.mark.parametrize("automatic_retries", [True, False])
def test_original_shared_checkpoint_is_still_validated(tmp_path, automatic_retries):
    path = tmp_path / "prior.json"
    path.write_text(json.dumps({"selected_case_ids": ["bleu-improvement",
        "gpt-parameter-evolution", "context-evolution"], "model_calls": 3,
        "automatic_retries": automatic_retries}), encoding="utf-8")
    if automatic_retries:
        with pytest.raises(ValueError, match="Historical budget"):
            prior_call_count(path, 39, 42)
    else:
        assert prior_call_count(path, 39, 42) == 3
        with pytest.raises(ValueError, match="batch authorization"):
            prior_call_count(path, 39, 41)


def test_known_completed_isolated_retrieval_namespace_only():
    collection = "official_remaining_bge_" + "a" * 32 + "_semantic_" + "b" * 10 + "_1024"
    report = {"production_library_modified": False, "status": "completed",
              "evaluation_kind": "official_local_bge_retrieval_not_generation",
              "temporary_vector_collection": collection}
    assert validate_index_report(report) == collection


@pytest.mark.parametrize("change", [
    {"production_library_modified": True},
    {"temporary_vector_collection": "production_chunks"},
    {"status": "running"},
    {"status": "error"},
    {"evaluation_kind": "untrusted_index_export"},
])
def test_production_or_unfinished_namespace_is_not_reused(change):
    report = {"production_library_modified": False, "status": "completed",
              "evaluation_kind": "official_local_bge_retrieval_not_generation",
              "temporary_vector_collection": "official_remaining_bge_" + "a" * 32
              + "_semantic_" + "b" * 10 + "_1024"}
    with pytest.raises(ValueError):
        validate_index_report({**report, **change})


def test_source_normalization_never_changes_a_numerical_answer():
    assert normalized("60 M") == normalized("60M")
    assert normalized("60M") != normalized("80M")
    assert normalized("1.3B") != normalized("13B")
    assert normalized("classiﬁcation") == normalized("classification")


def fixture():
    ast = {"pages": [{"page_number": 1, "width": 600, "height": 800,
                      "blocks": [{"block_id": "p1-b1", "text": "Trained on 60 GPUs.",
                                  "type": "text", "bbox": [60, 90, 250, 110]}]}]}
    anchor = EvidenceAnchor(evidence_id="E1", document_id="doc", page_number=1,
        block_ids=["p1-b1"], bbox=[60, 90, 250, 110], quote="Trained on 60 GPUs.", score=1,
        recall_mode="hybrid", retrieval_mode="reranked")
    case = {"page": 1, "quote": "60 GPUs", "answer_groups": [["60"], ["GPU"]], "ast": ast}
    response = SimpleNamespace(evidence=[anchor],
        claims=[AnswerClaim(text="使用60 GPU。", evidence_ids=["E1"])],
        provider="deepseek", insufficient_evidence=False)
    return case, response, anchor


def test_exact_requested_source_and_scoped_anchor_are_required():
    case, response, _ = fixture()
    assert check_response(case, response, "doc")["status"] == "machine_passed_pending_source_review"
    assert check_response(case, response, "other")["status"] == "failed"
    case["page"] = 2
    assert check_response(case, response, "doc")["facts"] == [False, False]


@pytest.mark.parametrize("change", [
    {"block_ids": ["p1-fabricated"]},
    {"quote": "Trained on 80 GPUs."},
    {"bbox": [300, 300, 400, 400]},
    {"bbox": [60, 90, float("nan"), 110]},
    {"page_number": 2},
])
def test_fabricated_text_geometry_or_node_never_passes(change):
    case, _, anchor = fixture()
    draft = ChunkDraft(0, 1, ["p1-b1"], [60, 90, 250, 110], None, "Trained on 60 GPUs.")
    assert not source_anchor_valid(anchor.model_copy(update=change), case["ast"], [draft])


def test_correct_keywords_with_unknown_citation_or_fallback_do_not_pass():
    case, response, _ = fixture()
    response.claims = [AnswerClaim(text="60 GPUs", evidence_ids=["FAKE"])]
    assert check_response(case, response, "doc")["status"] == "failed"
    response.claims = [AnswerClaim(text="60 GPUs", evidence_ids=["E1"])]
    response.provider = "extractive"
    assert "no_complete_real_model_answer" in check_response(case, response, "doc")["failures"]


def test_content_drift_has_a_specific_exception_and_does_not_change_saved_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    case, _, _ = fixture()
    with Session(engine) as session:
        doc = Document(id="doc", original_filename="example.pdf", storage_key="test",
            sha256="a" * 64, size_bytes=1, status=DocumentStatus.READY)
        session.add(doc)
        session.flush()
        replace_document_chunks(session, doc.id, case["ast"])
        session.commit()
        verify_chunks(session, doc.id, case["ast"])
        changed, _, _ = fixture()
        changed["ast"]["pages"][0]["blocks"][0]["text"] = "Trained on 80 GPUs."
        with pytest.raises(VerifiedAstMismatch) as error:
            verify_chunks(session, doc.id, changed["ast"])
        assert error.value.details == {"current_chunks": 1, "saved_chunks": 1,
                                      "missing_current_chunks": 1, "obsolete_saved_chunks": 1}
        verify_chunks(session, doc.id, case["ast"])
        with pytest.raises(ValueError, match="no indexable evidence") as empty:
            verify_chunks(session, doc.id, {"pages": []})
        assert not isinstance(empty.value, VerifiedAstMismatch)
    engine.dispose()


def test_saved_read_only_index_cannot_be_used_for_rebuilding():
    readonly = object.__new__(ReadOnlyOfficialIndex)
    with pytest.raises(ValueError, match="Never rebuild"):
        index_current_ast(None, readonly, {}, None)


@pytest.mark.parametrize("indexed", [0, 2])
def test_incomplete_fresh_index_cannot_pass(indexed, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    case, _, _ = fixture()
    case.update(filename="example.pdf", sha256="a" * 64)
    case["ast"].update(title="Example", authors=[])
    with Session(engine) as session:
        with pytest.raises(ValueError, match="index is incomplete"):
            index_current_ast(session, SimpleNamespace(index_document=lambda *_: indexed),
                              case, tmp_path)
    engine.dispose()


def test_drift_rebuild_reuses_only_disposable_sqlite_identity():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    case, _, _ = fixture()
    case.update(filename="example.pdf", sha256="a" * 64)
    case["ast"].update(title="Example", authors=[])
    calls = []
    with Session(engine) as session:
        doc = Document(id="doc", original_filename="example.pdf", storage_key="test",
            sha256=case["sha256"], size_bytes=1, status=DocumentStatus.READY)
        session.add(doc)
        session.flush()
        fresh = SimpleNamespace(index_document=lambda _session, doc_id: calls.append(doc_id) or 1)
        assert index_current_ast(session, fresh, case, None, doc) == "doc"
        assert calls == ["doc"]
        verify_chunks(session, doc.id, case["ast"])
    engine.dispose()
