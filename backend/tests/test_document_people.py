from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import documents as routes
from app.main import app
from app.models.document import DocumentStatus
from app.services.document_people import people_metadata


def parsed_people():
    credits = {"page_number": 1, "block_id": "p1-b1", "bbox": [10, 10, 500, 25],
               "text": "OpenScience Team"}
    first = {"page_number": 1, "block_id": "p1-b2", "bbox": [10, 30, 500, 50],
             "text": "Key Contributors Alice One, Carlos"}
    continuation = {"page_number": 2, "block_id": "p2-b1", "bbox": [10, 10, 500, 25],
                    "text": "Muñoz Ferrandis, Bob Two"}
    return {
        "authors": ["OpenScience Team"], "affiliations": [],
        "contributor_groups": [{"role": "Key Contributors",
                                "members": ["Alice One", "Carlos Muñoz Ferrandis", "Bob Two"],
                                "sources": [first, continuation]}],
        "pages": [
            {"page_number": 1, "blocks": [dict(credits, type="text"), dict(first, type="text")]},
            {"page_number": 2, "blocks": [dict(continuation, type="text"),
                                          {"block_id": "p2-b2", "type": "heading",
                                           "bbox": [10, 40, 500, 55], "text": "Abstract"}]},
        ],
    }


def test_compact_people_preserves_cross_page_source_and_not_global_author_ranks():
    parsed = parsed_people()
    before = deepcopy(parsed)
    result = people_metadata(parsed)
    assert result["authors"] == ["OpenScience Team"]
    assert result["affiliations"] == []
    assert [s["block_id"] for s in result["author_sources"]] == ["p1-b1"]
    group = result["contributor_groups"][0]
    assert group["members"][1] == "Carlos Muñoz Ferrandis"
    assert [s["page_number"] for s in group["sources"]] == [1, 2]
    assert "Carlos Muñoz Ferrandis" not in group["sources"][0]["text"]
    assert "pages" not in result and "rank" not in group
    assert result["contributor_status"] == "available"
    assert before == parsed


@pytest.mark.parametrize("damage", [
    "text", "bbox", "member", "body", "nonfinite", "role", "forged_member",
])
def test_invalid_group_hidden_as_whole_not_silently_partial(damage):
    parsed = parsed_people()
    group = parsed["contributor_groups"][0]
    if damage == "text":
        group["sources"][1]["text"] = "Forged Name"
    elif damage == "bbox":
        group["sources"][1]["bbox"] = [0, 0, 1, 1]
    elif damage == "member":
        group["members"] = [None]
    elif damage == "body":
        parsed["pages"][1]["blocks"].insert(0, {"type": "heading", "bbox": [0, 0, 500, 5],
                                                "text": "Abstract", "block_id": "p2-body"})
    elif damage == "role":
        group["role"] = "!!!"
    elif damage == "forged_member":
        group["members"] = ["Nonexistent Person"]
    else:
        group["sources"][1]["bbox"] = [0, 0, 1, float("inf")]
        parsed["pages"][1]["blocks"][0]["bbox"] = group["sources"][1]["bbox"]
    result = people_metadata(parsed)
    assert result["contributor_status"] == "unverified"
    assert result["contributor_groups"] == []


def test_legacy_and_ordinary_documents_have_different_empty_states():
    assert people_metadata({"authors": ["Alice One"]})["contributor_status"] == "legacy"
    result = people_metadata({"authors": ["Alice One"], "contributor_groups": []})
    assert result["authors"] == ["Alice One"]
    assert result["author_sources"] == []
    assert result["contributor_status"] == "not_detected"


def test_malformed_group_field_is_not_disguised_as_a_legacy_ast():
    assert people_metadata({"contributor_groups": {}})["contributor_status"] == "unverified"


def test_people_endpoint_compact_json(monkeypatch):
    monkeypatch.setattr(routes, "_get_document",
                        lambda *_: SimpleNamespace(status=DocumentStatus.READY))
    monkeypatch.setattr(routes, "read_parsed_document", lambda _: parsed_people())
    response = TestClient(app).get("/api/v1/documents/test-doc/people")
    assert response.status_code == 200
    assert response.json()["document_id"] == "test-doc"
    assert response.json()["contributor_status"] == "available"
    assert "pages" not in response.json()


@pytest.mark.parametrize("state,ast,expected", [
    (DocumentStatus.PROCESSING, {}, 409), (DocumentStatus.READY, None, 404),
])
def test_people_endpoint_unready_or_missing_ast(monkeypatch, state, ast, expected):
    monkeypatch.setattr(routes, "_get_document", lambda *_: SimpleNamespace(status=state))
    monkeypatch.setattr(routes, "read_parsed_document", lambda _: ast)
    assert TestClient(app).get("/api/v1/documents/test-doc/people").status_code == expected


def test_people_endpoint_missing_document(monkeypatch):
    def missing(*_):
        raise HTTPException(status_code=404, detail="文献不存在。")

    monkeypatch.setattr(routes, "_get_document", missing)
    assert TestClient(app).get("/api/v1/documents/missing/people").status_code == 404
