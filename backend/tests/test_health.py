from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "environment" in body


def test_health_response_shape() -> None:
    response = client.get("/health")
    body = response.json()
    assert set(body.keys()) == {"status", "environment"}
