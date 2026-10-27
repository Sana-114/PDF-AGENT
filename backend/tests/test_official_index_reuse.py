import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.chunk import DocumentChunk
from app.models.document import Document, DocumentStatus
from app.services.chunking import build_chunk_drafts
from app.services.text_features import infer_source_type
from scripts import official_index_reuse as reuse
from scripts.official_functional_inputs import digest

NAMESPACE = "official_runtime_" + "a" * 32 + "_semantic_" + "b" * 10 + "_1024"


def fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(reuse, "TEMP_ROOT", tmp_path)
    root = tmp_path / "source"
    (root / "uploads").mkdir(parents=True)
    (root / "parsed").mkdir()
    pdf = root / "uploads/one.pdf"
    pdf.write_bytes(b"Synthetic test, never sent to a model")
    ast = {"title": "Synthetic method", "pages": [{"page_number": 1, "width": 600,
        "height": 800, "blocks": [{"block_id": "p1-b1", "type": "text",
            "text": "This synthetic method has a recorded training procedure and no private data.",
            "bbox": [20, 40, 500, 120]}]}]}
    path = root / "parsed/one.json"
    path.write_text(json.dumps(ast), encoding="utf-8")
    rows = [{"document_id": "one", "filename": "public.pdf", "source_sha256": digest(pdf),
             "ast_sha256": digest(path)}]
    (root / "fixture.json").write_text(json.dumps(rows), encoding="utf-8")
    meta = {"allow_local_bge": True, "production_library_modified": False,
            "source_fixture_sha256": digest(root / "fixture.json"), "indexed_chunks": 1,
            "vector_collection": NAMESPACE}
    (root / "server-source.json").write_text(json.dumps(meta), encoding="utf-8")
    return root, rows, ast, meta


def test_saved_metadata_pins_source_bytes_and_forbids_production_namespace(tmp_path, monkeypatch):
    root, rows, _, meta = fixtures(tmp_path, monkeypatch)
    assert reuse.saved_index_metadata(root, rows) == meta
    meta["vector_collection"] = "production_semantic_" + "b" * 10 + "_1024"
    (root / "server-source.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="namespace"):
        reuse.saved_index_metadata(root, rows)


@pytest.mark.parametrize("defect", ["pdf", "ast", "scope", "provenance", "fixture_sha", "count"])
def test_saved_metadata_refuses_drift_before_any_vector_access(tmp_path, monkeypatch, defect):
    root, rows, _, meta = fixtures(tmp_path, monkeypatch)
    if defect == "pdf":
        (root / "uploads/one.pdf").write_bytes(b"changed")
    elif defect == "ast":
        (root / "parsed/one.json").write_text("{}", encoding="utf-8")
    elif defect == "scope":
        rows = []
    elif defect == "provenance":
        meta["production_library_modified"] = True
    elif defect == "fixture_sha":
        meta["source_fixture_sha256"] = "changed"
    elif defect == "count":
        meta["indexed_chunks"] = 0
    (root / "server-source.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError):
        reuse.saved_index_metadata(root, rows)


class Client:
    def __init__(self, points):
        self.points = points

    def collection_exists(self, name):
        return name == NAMESPACE

    def scroll(self, **kwargs):
        assert kwargs["with_vectors"] is False
        return self.points, None

    def __getattr__(self, name):
        raise AssertionError("No vector mutation or embedding call allowed: " + name)


def points_from_ast(ast):
    result = []
    for d in build_chunk_drafts(ast):
        chunk_id = str(uuid4())
        result.append(SimpleNamespace(id=chunk_id, payload={
            "chunk_id": chunk_id, "document_id": "one", "document_title": ast["title"],
            "page_number": d.page_number, "section": d.section, "text": d.text,
            "block_ids": d.block_ids, "bbox": d.bbox,
            "source_type": infer_source_type(d.section, d.block_ids),
        }))
    return result


@pytest.mark.parametrize("defect", [None, "text", "title", "scope", "id", "bbox", "type", "count"])
def test_complete_payloads_verified_before_empty_local_sql_restore(tmp_path, monkeypatch, defect):
    root, rows, ast, meta = fixtures(tmp_path, monkeypatch)
    points = points_from_ast(ast)
    original = copy.deepcopy(points)
    if defect in {"text", "title", "scope", "id", "type"}:
        key = {"title": "document_title", "scope": "document_id", "id": "chunk_id",
               "type": "source_type"}.get(defect, defect)
        points[0].payload[key] = "changed"
    elif defect == "bbox":
        points[0].payload["bbox"] = [20, 700, 500, 780]
    elif defect == "count":
        points = []
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Document(id="one", title=ast["title"], original_filename="public.pdf",
            storage_key="one.pdf", size_bytes=1, sha256=rows[0]["source_sha256"],
            status=DocumentStatus.READY))
        session.commit()
        index = SimpleNamespace(collection_name=NAMESPACE, client=Client(points))
        if defect:
            with pytest.raises(ValueError):
                reuse.restore_verified_index(session, index, root, rows, meta)
            assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        else:
            assert reuse.restore_verified_index(session, index, root, rows, meta) == 1
            assert session.scalars(select(DocumentChunk)).one().id == original[0].id
            with pytest.raises(ValueError, match="empty"):
                reuse.restore_verified_index(session, index, root, rows, meta)


def test_reused_index_class_forbids_index_and_delete_even_if_endpoint_attempts_it():
    from scripts.evaluate_official_first_round import ReadOnlyOfficialIndex

    index = ReadOnlyOfficialIndex.__new__(ReadOnlyOfficialIndex)
    with pytest.raises(ValueError, match="forbids"):
        index.index_document(None, "one")
    with pytest.raises(ValueError, match="forbids"):
        index.delete_document("one")
