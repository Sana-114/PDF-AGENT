from fastapi.testclient import TestClient

from app.api.routes import agent as agent_routes
from app.llm.extractive import ExtractiveProvider
from app.main import app


def test_agent_status_and_skills_are_exposed(monkeypatch) -> None:
    monkeypatch.setattr(agent_routes, "get_llm_provider", ExtractiveProvider)
    with TestClient(app) as client:
        status_response = client.get("/api/v1/agent/status")
        skills_response = client.get("/api/v1/agent/skills")

    assert status_response.status_code == 200
    assert status_response.json()["provider"] == "extractive"
    assert status_response.json()["translation_configured"] is False
    assert status_response.json()["retrieval_mode"] in {
        "lexical",
        "hybrid_qdrant_rrf",
        "hybrid_qdrant_rrf+cross_encoder",
    }
    assert status_response.json()["embedding_provider"] in {"hash", "openai-compatible"}
    assert status_response.json()["reranker_provider"] in {"disabled", "tei"}
    if status_response.json()["reranker_provider"] == "disabled":
        assert status_response.json()["reranker_model"] is None
    else:
        assert status_response.json()["reranker_model"] == "BAAI/bge-reranker-v2-m3"
    assert skills_response.status_code == 200
    assert {item["name"] for item in skills_response.json()} == {
        "get_document_outline",
        "get_document_structure",
        "get_document_table",
        "search_evidence",
    }
