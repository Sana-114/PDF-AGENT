import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from official_vector_audit import AuditedQdrantClient, ReadOnlyAuditedQdrantClient  # noqa: E402

COLLECTION = "official_comparison_batch_unit"


def audited(client, tmp_path):
    rows, saves = [], []
    wrapper = AuditedQdrantClient(client, COLLECTION, rows, lambda: saves.append(1), tmp_path)
    return wrapper, rows, saves


def test_success_has_same_request_and_result_without_additional_calls(tmp_path):
    requests = []
    result = object()

    def count(**kwargs):
        requests.append(kwargs)
        return result

    wrapper, rows, saves = audited(SimpleNamespace(count=count), tmp_path)
    assert wrapper.count(collection_name=COLLECTION, exact=True) is result
    assert requests == [{"collection_name": COLLECTION, "exact": True}]
    assert rows[0]["status"] == "returned" and rows[0]["duration_ms"] >= 0
    assert len(saves) == 2 and rows[0]["automatic_retries"] == 0


def test_failed_write_retains_batch_and_root_cause_but_never_retries(tmp_path):
    requests = []

    class WrappedError(Exception):
        pass

    def upsert(**kwargs):
        requests.append(kwargs)
        try:
            raise TimeoutError("synthetic timeout")
        except TimeoutError as cause:
            raise WrappedError("synthetic wrapper") from cause

    point = SimpleNamespace(model_dump=lambda **kwargs: {"id": "one", "vector": [0.1]})
    wrapper, rows, _ = audited(SimpleNamespace(upsert=upsert), tmp_path)
    with pytest.raises(WrappedError):
        wrapper.upsert(collection_name=COLLECTION, points=[point], wait=True)
    assert len(requests) == 1
    assert [e["type"] for e in rows[0]["error_chain"]] == ["WrappedError", "TimeoutError"]
    saved = json.loads((tmp_path / rows[0]["request_artifact"]).read_bytes())
    assert saved == {"collection_name": COLLECTION, "wait": True,
                     "points": [{"id": "one", "vector": [0.1]}]}
    assert rows[0]["frames"] and len(rows[0]["request_sha256"]) == 64


def test_snapshot_failure_cannot_replace_original_network_error(tmp_path):
    def upsert(**kwargs):
        raise TimeoutError("original timeout")

    wrapper, rows, _ = audited(SimpleNamespace(upsert=upsert), tmp_path)
    with pytest.raises(TimeoutError, match="original timeout"):
        wrapper.upsert(collection_name=COLLECTION, points=[object()], wait=True)
    assert rows[0]["snapshot_error_type"] == "AttributeError"


def test_rejects_an_unrelated_collection_before_network_access(tmp_path):
    calls = []
    client = SimpleNamespace(count=lambda **kwargs: calls.append(kwargs))
    wrapper, rows, _ = audited(client, tmp_path)
    with pytest.raises(ValueError):
        wrapper.count(collection_name="production")
    assert not rows and not calls
    with pytest.raises(ValueError):
        AuditedQdrantClient(client, "production", [], lambda: None, tmp_path)


@pytest.mark.parametrize("method", [
    "upsert", "delete", "create_collection", "create_payload_index", "delete_collection",
    "http", "grpc_points", "batch_update_points", "unknown_method",
])
def test_reuse_client_blocks_mutations_and_raw_handles_before_delegate_access(tmp_path, method):
    class Untouched:
        def __getattr__(self, name):
            raise AssertionError("Delegate must not be inspected: " + name)

    namespace = "official_runtime_" + "a" * 32 + "_semantic_" + "b" * 10 + "_1024"
    wrapper = ReadOnlyAuditedQdrantClient(Untouched(), namespace, [], lambda: None, tmp_path)
    with pytest.raises(ValueError, match="forbids"):
        getattr(wrapper, method)


def test_reuse_client_records_exact_read_and_refuses_cross_namespace(tmp_path):
    calls, rows = [], []
    namespace = "official_runtime_" + "a" * 32 + "_semantic_" + "b" * 10 + "_1024"
    result = object()

    def scroll(**kwargs):
        calls.append(kwargs)
        return result

    wrapper = ReadOnlyAuditedQdrantClient(
        SimpleNamespace(scroll=scroll), namespace, rows, lambda: None, tmp_path,
    )
    args = {"collection_name": namespace, "with_vectors": False, "limit": 128}
    assert wrapper.scroll(**args) is result
    assert calls == [args] and rows[0]["operation"] == "scroll"
    assert rows[0]["status"] == "returned"
    with pytest.raises(ValueError, match="outside"):
        wrapper.scroll(collection_name="production")
    assert len(calls) == 1
    with pytest.raises(ValueError, match="namespace"):
        ReadOnlyAuditedQdrantClient(object(), "production", [], lambda: None, tmp_path)
