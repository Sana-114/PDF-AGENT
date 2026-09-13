from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routes import documents as document_routes
from app.main import app
from app.services.document_translation_export import build_document_translation_export
from app.services.translation_store import TranslationStore


def _manifest() -> dict:
    return {
        "document_id": "doc-export",
        "source_fingerprint": "a" * 64,
        "target_language": "zh",
        "provider": "stub",
        "model": "translator-v1",
        "status": "completed",
        "page_count": 2,
    }


def _page(page_number: int, source: str, translation: str) -> dict:
    return {
        "document_id": "doc-export",
        "page_number": page_number,
        "segments": [
            {
                "block_id": f"p{page_number}-b1",
                "bbox": [1, 2, 3, 4],
                "source_text": source,
                "translation": translation,
            }
        ],
    }


def test_translation_export_preserves_page_order_and_protected_text() -> None:
    export = build_document_translation_export(
        title="Attention Translation",
        original_filename="attention.pdf",
        manifest=_manifest(),
        pages=[
            _page(1, "beta_2 = 0.98", "β_2 = 0.98"),
            _page(2, "```python\nprint('ok')\n```", "```python\nprint('ok')\n```"),
        ],
        output_format="markdown",
        mode="bilingual",
    )

    assert export.filename == "attention-zh.md"
    assert export.media_type == "text/markdown"
    assert export.content.index("## Page 1") < export.content.index("## Page 2")
    assert "β_2 = 0.98" in export.content
    assert "```python\nprint('ok')\n```" in export.content
    assert "**Original**" in export.content


def test_html_translation_export_escapes_document_content() -> None:
    export = build_document_translation_export(
        title="Paper <Draft>",
        original_filename="paper.pdf",
        manifest=_manifest(),
        pages=[_page(1, "<script>alert(1)</script>", "安全译文 & 公式 x < y")],
        output_format="html",
        mode="translation",
    )

    assert export.filename == "paper-zh.html"
    assert "<title>Paper &lt;Draft&gt;</title>" in export.content
    assert "<script>alert(1)</script>" not in export.content
    assert "安全译文 &amp; 公式 x &lt; y" in export.content
    assert 'grid-template-columns: 1fr;' in export.content


def test_completed_translation_can_be_downloaded_as_html(monkeypatch, tmp_path) -> None:
    store = TranslationStore(tmp_path / "translations")
    store.prepare(
        document_id="doc-export",
        target_language="zh",
        source_fingerprint="a" * 64,
        page_count=2,
        provider="stub",
        model="translator-v1",
        activate=True,
    )
    store.write_page("doc-export", "zh", 1, _page(1, "First", "第一页"))
    store.write_page("doc-export", "zh", 2, _page(2, "Second", "第二页"))
    store.set_status("doc-export", "zh", "completed")
    monkeypatch.setattr(document_routes, "translation_store", store)
    monkeypatch.setattr(
        document_routes,
        "_get_document",
        lambda document_id, db: SimpleNamespace(
            id=document_id,
            title="测试论文",
            original_filename="测试论文.pdf",
            sha256="a" * 64,
        ),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/documents/doc-export/translations/zh/export",
            params={"format": "html", "mode": "bilingual"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert "第一页" in response.text
    assert "第二页" in response.text
    assert response.text.index("Page 1") < response.text.index("Page 2")


def test_translation_export_rejects_incomplete_job(monkeypatch, tmp_path) -> None:
    store = TranslationStore(tmp_path / "translations")
    store.prepare(
        document_id="doc-export",
        target_language="en",
        source_fingerprint="b" * 64,
        page_count=2,
        provider="stub",
        model="translator-v1",
        activate=True,
    )
    store.set_status("doc-export", "en", "processing")
    monkeypatch.setattr(document_routes, "translation_store", store)
    monkeypatch.setattr(
        document_routes,
        "_get_document",
        lambda document_id, db: SimpleNamespace(
            id=document_id,
            title="Paper",
            original_filename="paper.pdf",
            sha256="b" * 64,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/documents/doc-export/translations/en/export")

    assert response.status_code == 409
    assert response.json()["detail"] == "整篇翻译完成后才能导出。"
