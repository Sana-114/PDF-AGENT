from fastapi.testclient import TestClient

from app.main import app


def test_agent_status_and_skills_are_exposed() -> None:
    with TestClient(app) as client:
        status_response = client.get("/api/v1/agent/status")
        skills_response = client.get("/api/v1/agent/skills")

    assert status_response.status_code == 200
    assert status_response.json()["provider"] == "extractive"
    assert skills_response.status_code == 200
    assert {item["name"] for item in skills_response.json()} == {
        "get_document_outline",
        "get_document_structure",
        "get_document_table",
        "search_evidence",
    }
