from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.2.0"


def test_cors_exposes_pdf_range_headers() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/health",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.status_code == 200
    exposed_headers = response.headers["access-control-expose-headers"].lower()
    assert "accept-ranges" in exposed_headers
    assert "content-length" in exposed_headers
    assert "content-range" in exposed_headers
