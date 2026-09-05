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
    """/health/ready probes the dependencies the app actually uses:
    Postgres (required -- flips overall readiness) and Kafka (Phase 12,
    reported for visibility only -- never flips readiness, per ADR-007;
    see app.api.health.get_readiness's docstring). Redis is provisioned
    but unused by application code, so it is not probed at all.

    ``client`` here is the async httpx fixture from conftest, not the
    module-level sync ``TestClient``.
    """
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["kafka"] == "not_configured"  # no KAFKA_BOOTSTRAP_SERVERS in tests
    assert "redis" not in body["checks"]


async def test_readiness_stays_ready_even_if_kafka_check_is_not_ok(
    client: AsyncClient,
) -> None:
    """Kafka being unreachable/unconfigured must never flip overall
    readiness to 'not_ready' -- ADR-007's whole point is that a Kafka
    outage never blocks request-serving.
    """
    response = await client.get("/health/ready")
    body = response.json()
    assert body["checks"]["kafka"] != "ok"
    assert body["status"] == "ready"
    assert response.status_code == 200
