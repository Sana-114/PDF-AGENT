import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import documents as routes
from app.main import app
from app.services.translation_store import TranslationStore


def cache(tmp_path, monkeypatch):
    store = TranslationStore(tmp_path / "translations")
    store.prepare(
        document_id="owned",
        target_language="zh",
        source_fingerprint="source",
        page_count=2,
        provider="test",
        model="v1",
        activate=True,
    )
    parsed = {
        "pages": [
            {
                "page_number": n,
                "blocks": [{"block_id": f"b{n}", "text": "Public text", "bbox": [1, 2, 3, 4]}],
            }
            for n in (1, 2)
        ]
    }
    for n in (1, 2):
        store.write_page(
            "owned",
            "zh",
            n,
            {
                "document_id": "owned",
                "page_number": n,
                "target_language": "zh",
                "source_language": "auto",
                "provider": "test",
                "model": "v1",
                "segments": [
                    {
                        "block_id": f"b{n}",
                        "source_text": "Public text",
                        "bbox": [1, 2, 3, 4],
                        "translation": "公开文本",
                    }
                ],
            },
        )
    store.set_status("owned", "zh", "completed")
    monkeypatch.setattr(routes, "translation_store", store)
    monkeypatch.setattr(routes, "read_parsed_document", lambda doc: parsed)
    monkeypatch.setattr(
        routes,
        "_get_document",
        lambda *args: SimpleNamespace(
            id="owned",
            sha256="source",
            page_count=2,
            title="Public",
            original_filename="public.pdf",
        ),
    )
    return store, store.job_dir("owned", "zh")


@pytest.mark.parametrize("bad_count", [0, 1, "broken", True, None, -1, 3])
def test_corrupt_manifest_count_cannot_certify_short_or_empty_export(
    tmp_path, monkeypatch, bad_count
):
    store, folder = cache(tmp_path, monkeypatch)
    path = folder / "manifest.json"
    data = json.loads(path.read_bytes())
    data["page_count"] = bad_count
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with TestClient(app) as client:
        status = client.get("/api/v1/documents/owned/translations/zh")
        assert status.status_code == 200 and status.json()["status"] == "failed"
        assert status.json()["page_count"] == 2
        for fmt in ("html", "markdown"):
            assert (
                client.get(
                    "/api/v1/documents/owned/translations/zh/export", params={"format": fmt}
                ).status_code
                == 409
            )
    assert path.read_bytes() == before
    assert store.read_manifest("owned", "zh")["page_count"] == bad_count


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_id", "other"),
        ("target_language", "en"),
        ("page_number", 2),
        ("page_number", True),
        ("source_language", "en"),
        ("provider", "other"),
        ("model", "other"),
        ("document_id", None),
        ("target_language", None),
    ],
)
def test_wrong_page_identity_or_provenance_is_not_served_or_exported(
    tmp_path, monkeypatch, field, value
):
    store, folder = cache(tmp_path, monkeypatch)
    path = folder / "page-000001.json"
    data = json.loads(path.read_bytes())
    if value is None:
        data.pop(field)
    else:
        data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    assert store.read_page("owned", "zh", 1) is None
    with TestClient(app) as client:
        assert client.get("/api/v1/documents/owned/translations/zh/pages/1").status_code == 404
        assert client.get("/api/v1/documents/owned/translations/zh/export").status_code == 409
        assert client.get("/api/v1/documents/owned/translations/zh").json()["status"] == "failed"
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("document_id", "other"), ("target_language", "en")])
def test_manifest_ownership_must_match_storage_scope(tmp_path, monkeypatch, field, value):
    store, folder = cache(tmp_path, monkeypatch)
    path = folder / "manifest.json"
    data = json.loads(path.read_bytes())
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    assert store.read_manifest("owned", "zh") is None
    with TestClient(app) as client:
        for suffix in ("", "/pages/1", "/export"):
            assert client.get("/api/v1/documents/owned/translations/zh" + suffix).status_code == 404
    assert path.read_bytes() == before
