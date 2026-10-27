import json
import sqlite3

import pytest

from scripts import prepare_official_browser_fixture as prepare
from scripts.official_functional_inputs import digest
from scripts.serve_official_acceptance_fixture import local_service_url
from scripts.verify_official_candidate_qa import comparison_checks


def saved_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "TEMP_ROOT", tmp_path)
    monkeypatch.setitem(prepare.select_sources.__globals__, "TEMP_ROOT", tmp_path)
    root = tmp_path / "isolated"
    (root / "uploads").mkdir(parents=True)
    (root / "parsed").mkdir()
    source = root / "uploads" / "document.pdf"
    source.write_bytes(b"Synthetic identity fixture, never sent externally")
    ast = root / "parsed" / "document.json"
    ast.write_text(json.dumps({"title": "Synthetic study", "authors": ["A. Author"],
                               "pages": [{"page_number": 1}]}), encoding="utf-8")
    row = {"document_id": "document", "filename": "one.pdf",
           "source_sha256": digest(source), "ast_sha256": digest(ast)}
    (root / "fixture.json").write_text(json.dumps([row]), encoding="utf-8")
    with sqlite3.connect(root / "acceptance.db") as db:
        db.execute("CREATE TABLE documents (id, sha256, original_filename, status, "
                   "page_count, parser_name, parser_version)")
        db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("document", row["source_sha256"], "one.pdf", "ready", 1, "native", "v1"))
    return root


def test_saved_subset_checks_identity_without_mutating_source_database(tmp_path, monkeypatch):
    root = saved_fixture(tmp_path, monkeypatch)
    before = digest(root / "acceptance.db")
    inputs = prepare.saved_inputs(root, ["one.pdf"])
    assert digest(root / "acceptance.db") == before
    assert inputs[0]["row"]["parser_version"] == "v1"
    assert inputs[0]["ast_sha256"] == digest(root / "parsed" / "document.json")


@pytest.mark.parametrize("names", [[], ["one.pdf", "one.pdf"]])
def test_saved_subset_rejects_empty_or_duplicate_selection(tmp_path, monkeypatch, names):
    root = saved_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="nonempty unique"):
        prepare.saved_inputs(root, names)


def test_saved_subset_rejects_changed_pdf_before_preparing_a_new_library(tmp_path, monkeypatch):
    root = saved_fixture(tmp_path, monkeypatch)
    (root / "uploads" / "document.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="identity changed"):
        prepare.saved_inputs(root, ["one.pdf"])


def test_saved_subset_rejects_inconsistent_sql_page_metadata(tmp_path, monkeypatch):
    root = saved_fixture(tmp_path, monkeypatch)
    with sqlite3.connect(root / "acceptance.db") as db:
        db.execute("UPDATE documents SET page_count = 2")
    with pytest.raises(ValueError, match="identity differ"):
        prepare.saved_inputs(root, ["one.pdf"])


@pytest.mark.parametrize("url", ["http://127.0.0.1:8001/v1", "http://localhost:7333/"])
def test_local_bge_services_are_explicit_loopback_endpoints(url):
    assert local_service_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", ["https://example.test:443", "http://0.0.0.0:8001",
    "http://user:secret@localhost:8001", "http://localhost", "http://localhost:8001?key=x",
    "http://localhost:8001#fragment"])
def test_runtime_acceptance_rejects_remote_ambiguous_or_credential_urls(url):
    with pytest.raises(ValueError, match="loopback"):
        local_service_url(url)


def comparison_payload():
    return {"provider": "deepseek", "insufficient_evidence": False,
            "claims": [{"text": "Frozen LoRA; QLoRA uses 4-bit NF4, double quantization "
                                 "and paged optimizers.", "evidence_ids": ["E1", "E2"]}],
            "evidence": [{"evidence_id": key, "document_id": doc,
                          "recall_mode": "hybrid", "retrieval_mode": "reranked"}
                         for key, doc in [("E1", "one"), ("E2", "two")]]}


def test_candidate_comparison_requires_both_originals_in_one_published_claim():
    payload = comparison_payload()
    assert all(comparison_checks(payload, ["one", "two"]).values())
    payload["claims"][0]["evidence_ids"] = ["E1"]
    payload["claims"].append({"text": "QLoRA.", "evidence_ids": ["E2"]})
    assert not comparison_checks(payload, ["one", "two"])["one_claim_has_both_originals"]


def test_candidate_comparison_does_not_hide_lexical_fallback_or_extra_papers():
    payload = comparison_payload()
    payload["evidence"][0]["recall_mode"] = "lexical"
    assert not comparison_checks(payload, ["one", "two"])["all_used_anchors_hybrid_reranked"]
    payload["evidence"][0]["document_id"] = "outside"
    assert not comparison_checks(payload, ["one", "two"])["all_published_sources_in_scope"]


def test_policy_candidate_checks_never_treat_separate_single_sources_as_comparison():
    from verify_official_candidate_qa import policy_checks

    payload = comparison_payload()
    payload["claims"][0]["text"] = (
        "最优策略有闭式表达，通过分类损失学习；微调时免去从语言模型采样。")
    assert all(policy_checks(payload, ["one", "two"]).values())
    payload["claims"][0]["evidence_ids"] = ["E1"]
    payload["claims"].append({"text": "另一个方法。", "evidence_ids": ["E2"]})
    assert not policy_checks(payload, ["one", "two"])["one_comparison_cites_both_originals"]


def test_policy_counterexample_checks_refusal_and_scoped_retrieval_separately():
    from verify_official_candidate_qa import policy_checks

    payload = comparison_payload()
    payload["claims"][0]["text"] = (
        "最优策略有闭式表达，通过分类损失学习，微调时无需从语言模型采样，"
        "不意味着所有流程无采样。")
    payload["claims"][0]["evidence_ids"] = ["E1"]
    assert all(policy_checks(payload, ["one"], counterexample=True).values())
    payload["evidence"][0]["recall_mode"] = "lexical"
    assert not policy_checks(payload, ["one"], counterexample=True)[
        "published_sources_scoped_and_hybrid_reranked"]


def test_policy_check_accepts_binary_cross_entropy_synonym_not_just_one_chinese_label():
    from verify_official_candidate_qa import policy_checks

    payload = comparison_payload()
    payload["claims"][0]["text"] = (
        "最优策略可闭式提取，使用二值交叉熵目标学习；微调时不从语言模型采样，"
        "而非全流程免采样。")
    payload["claims"][0]["evidence_ids"] = ["E1"]
    assert all(policy_checks(payload, ["one"], counterexample=True).values())


@pytest.mark.parametrize("denial", ["并不表示", "并不表明", "不意味着"])
def test_policy_checker_accepts_explicit_denial_synonyms_but_keeps_other_requirements(denial):
    from verify_official_candidate_qa import policy_checks

    payload = comparison_payload()
    payload["claims"][0].update(
        text=f"最优策略有闭式表达，用分类损失学习；微调时免去从语言模型采样，{denial}全流程无采样。",
        evidence_ids=["E1"],
    )
    assert all(policy_checks(payload, ["one"], counterexample=True).values())
    payload["claims"][0]["text"] = f"最优策略有闭式表达，{denial}网络参数可一次求出。"
    checks = policy_checks(payload, ["one"], counterexample=True)
    assert checks["explicitly_rejects_overgeneralization"]
    assert not checks["sampling_object_and_stage_in_same_published_claim"]
    assert not checks["policy_object_and_learning_objective_present"]


def test_policy_check_accepts_explicit_binary_classification_cross_entropy_not_bare_accuracy():
    from verify_official_candidate_qa import policy_checks

    payload = comparison_payload()
    payload["claims"][0]["text"] = "最优策略可闭式提取，通过二分类/交叉熵目标学习。"
    assert policy_checks(payload, ["one", "two"])["policy_object_and_learning_objective_present"]
    payload["claims"][0]["text"] = "最优策略可闭式提取，测试准确率有所提高。"
    result = policy_checks(payload, ["one", "two"])
    assert not result["policy_object_and_learning_objective_present"]


def test_candidate_qa_records_incomplete_startup_without_cloud_calls(tmp_path, monkeypatch):
    import json
    import sys

    from scripts import verify_official_candidate_qa as verifier

    monkeypatch.setattr(sys, "argv", ["verify", "--fixture", str(tmp_path),
                                    "--api", "http://127.0.0.1:8018", "--output",
                                    str(tmp_path), "--allow-cloud"])
    monkeypatch.setattr(verifier, "select_sources", lambda *args: [])
    monkeypatch.setattr(verifier, "mechanism_tasks", lambda *args: ([], None))
    monkeypatch.setattr(verifier, "isolated_output", lambda path: path)

    def forbidden_client(*args, **kwargs):
        raise AssertionError("Startup failure must be recorded before any HTTP client")

    monkeypatch.setattr(verifier.httpx, "Client", forbidden_client)
    assert verifier.main() == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["generation_requests"] == 0
    assert report["server_source_sha256"] is None
    assert "has not completed startup" in report["error"]


def saved_evidence_inputs(tmp_path, monkeypatch):
    from scripts import verify_official_candidate_qa as verifier

    monkeypatch.setattr(verifier, "TEMP_ROOT", tmp_path)
    (tmp_path / "parsed").mkdir()
    text = "A public synthetic method describes its training procedure without private content."
    ast = {"pages": [{"page_number": 1, "width": 600, "height": 800, "blocks": [
        {"block_id": "body", "type": "text", "text": text, "bbox": [20, 50, 500, 100]},
    ]}]}
    (tmp_path / "parsed/one.json").write_text(json.dumps(ast), encoding="utf-8")
    rows = [{"document_id": "one"}]
    tasks = [("fixed", {"question": "A fixed method question.", "document_ids": ["one"],
                         "top_k": 1})]
    raw = {"sources": rows, "cases": [{"case_id": "fixed", "question": tasks[0][1]["question"],
        "response": {"answer": "PRIVATE UNSUPPORTED ANSWER MUST NEVER ENTER THE POOL",
                     "evidence": [{"evidence_id": "E1", "document_id": "one", "page_number": 1,
                        "block_ids": ["body"], "bbox": [20, 50, 500, 100], "score": 0.8,
                        "quote": text, "recall_mode": "hybrid", "retrieval_mode": "reranked"}]}}]}
    return verifier, rows, tasks, raw


def test_saved_retrieval_returns_only_anchors_not_saved_answers(tmp_path, monkeypatch):
    verifier, rows, tasks, raw = saved_evidence_inputs(tmp_path, monkeypatch)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = verifier.saved_retrieval_cases(path, tmp_path, rows, tasks)
    assert len(result) == len(result[0]) == 1
    assert "PRIVATE" not in json.dumps(result[0][0].model_dump())


@pytest.mark.parametrize("defect", ["question", "source", "scope", "quote", "bbox", "mode"])
def test_saved_retrieval_refuses_changed_question_private_scope_or_drift_before_billing(
    tmp_path, monkeypatch, defect,
):
    verifier, rows, tasks, raw = saved_evidence_inputs(tmp_path, monkeypatch)
    case = raw["cases"][0]
    e = case["response"]["evidence"][0]
    if defect == "question":
        case["question"] = "An altered question."
    elif defect == "source":
        raw["sources"] = []
    elif defect == "scope":
        e["document_id"] = "private"
    elif defect == "quote":
        e["quote"] = "A fabricated source statement."
    elif defect == "bbox":
        e["bbox"] = [30, 700, 500, 780]
    elif defect == "mode":
        e["recall_mode"] = "lexical"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        verifier.saved_retrieval_cases(path, tmp_path, rows, tasks)
