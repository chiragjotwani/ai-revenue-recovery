from fastapi.testclient import TestClient
from httpx import AsyncClient

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


async def test_readiness_reports_database_ok_when_reachable(client: AsyncClient) -> None:
    """/health/ready probes the dependencies the app actually uses (Postgres
    only -- Redis is provisioned but unused by application code).

    ``client`` here is the async httpx fixture from conftest, not the
    module-level sync ``TestClient``.
    """
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok"}
    assert "redis" not in body["checks"]
