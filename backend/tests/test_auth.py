"""API-key auth and role-based authorization (Phase 15: Security &
Fintech Hardening).

Uses the real ASGI app end to end (not a unit test of ``app.core.auth``
in isolation) -- what matters is that the actual routes are actually
guarded, not merely that the dependency function works if called.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import Role, parse_api_keys
from app.db.session import AsyncSessionLocal, get_db_session
from app.main import app
from tests.conftest import TEST_READONLY_API_KEY


def test_parse_api_keys_reads_role_per_key() -> None:
    keys = parse_api_keys("abc:operator, def:readonly")
    assert keys == {"abc": Role.OPERATOR, "def": Role.READONLY}


def test_parse_api_keys_empty_string_is_empty_map() -> None:
    assert parse_api_keys("") == {}


@pytest.mark.parametrize(
    "malformed",
    ["novalue:", "novalue", "key:admin", ":operator"],
)
def test_parse_api_keys_rejects_malformed_entries(malformed: str) -> None:
    with pytest.raises(ValueError):
        parse_api_keys(malformed)


async def _unauthenticated_client() -> AsyncClient:
    async def _override() -> object:
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_mutating_endpoint_without_api_key_is_401() -> None:
    async with await _unauthenticated_client() as client:
        response = await client.post("/recovery/cases", json={"payment_id": str(uuid.uuid4())})
    app.dependency_overrides.clear()
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_readonly_endpoint_without_api_key_is_401() -> None:
    async with await _unauthenticated_client() as client:
        response = await client.get("/recovery/cases")
    app.dependency_overrides.clear()
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_api_key_is_401() -> None:
    async def _override() -> object:
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": "not-a-real-key"}
    ) as client:
        response = await client.get("/recovery/cases")
    app.dependency_overrides.clear()
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_readonly_key_cannot_call_mutating_endpoint() -> None:
    async def _override() -> object:
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": TEST_READONLY_API_KEY},
    ) as client:
        response = await client.post("/recovery/cases", json={"payment_id": str(uuid.uuid4())})
    app.dependency_overrides.clear()
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_readonly_key_can_call_readonly_endpoint() -> None:
    async def _override() -> object:
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": TEST_READONLY_API_KEY},
    ) as client:
        response = await client.get("/recovery/cases")
    app.dependency_overrides.clear()
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_operator_key_can_call_both_readonly_and_mutating_endpoints(
    client: AsyncClient,
) -> None:
    list_response = await client.get("/recovery/cases")
    assert list_response.status_code == 200

    open_response = await client.post("/recovery/cases", json={"payment_id": str(uuid.uuid4())})
    # 404 (unknown payment) proves the request reached the handler past
    # auth -- the point of this test is authorization, not case-opening.
    assert open_response.status_code == 404


@pytest.mark.asyncio
async def test_health_and_metrics_require_no_api_key() -> None:
    async with await _unauthenticated_client() as client:
        health = await client.get("/health")
        ready = await client.get("/health/ready")
        metrics = await client.get("/metrics")
    app.dependency_overrides.clear()
    assert health.status_code == 200
    assert ready.status_code == 200
    assert metrics.status_code == 200


def test_production_startup_refuses_empty_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(environment="production", api_keys_raw=""),
    )
    with pytest.raises(RuntimeError, match="API_KEYS must be configured"):
        create_app()


def test_production_startup_accepts_configured_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(environment="production", api_keys_raw="prodkey:operator"),
    )
    # Should not raise.
    create_app()
