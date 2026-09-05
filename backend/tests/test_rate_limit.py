"""Rate limiting (Phase 15: Security & Fintech Hardening).

Exercises ``app.core.rate_limit`` directly against a real Redis instance
(per project policy: no mocking the infrastructure this system actually
depends on) rather than only unit-testing the fixed-window arithmetic in
isolation. Uses ``httpx.ASGITransport`` against a plain Starlette app,
the same async-native transport every other test in this suite uses --
``starlette.testclient.TestClient`` runs its own internal event loop,
which is incompatible with an async Redis client built on the test's own
pytest-asyncio loop.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimitMiddleware, _client_identity


async def _ok(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _build_app(redis_client: Redis) -> Starlette:
    app = Starlette(routes=[Route("/thing", _ok)])
    app.add_middleware(RateLimitMiddleware, redis_client=redis_client)
    return app


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(get_settings().redis_url)
    yield client
    await client.aclose()


def _settings_with_limit(limit: int) -> Settings:
    return Settings(rate_limit_requests_per_minute=limit)


async def test_requests_under_the_limit_all_succeed(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.rate_limit.get_settings", lambda: _settings_with_limit(5))
    app = _build_app(redis_client)
    transport = ASGITransport(app=app)
    headers = {"X-API-Key": f"rl-test-{uuid.uuid4()}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        for _ in range(5):
            response = await client.get("/thing")
            assert response.status_code == 200


async def test_requests_over_the_limit_are_429(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.rate_limit.get_settings", lambda: _settings_with_limit(3))
    app = _build_app(redis_client)
    transport = ASGITransport(app=app)
    headers = {"X-API-Key": f"rl-test-{uuid.uuid4()}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        statuses = [(await client.get("/thing")).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]


async def test_different_api_keys_have_independent_budgets(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.rate_limit.get_settings", lambda: _settings_with_limit(1))
    app = _build_app(redis_client)
    transport = ASGITransport(app=app)
    key_a = f"rl-test-a-{uuid.uuid4()}"
    key_b = f"rl-test-b-{uuid.uuid4()}"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response_a = await client.get("/thing", headers={"X-API-Key": key_a})
        response_b = await client.get("/thing", headers={"X-API-Key": key_b})
    assert response_a.status_code == 200
    assert response_b.status_code == 200


def test_health_and_metrics_paths_are_exempt() -> None:
    assert "/health" in RateLimitMiddleware._EXEMPT_PATHS
    assert "/health/ready" in RateLimitMiddleware._EXEMPT_PATHS
    assert "/metrics" in RateLimitMiddleware._EXEMPT_PATHS


def test_client_identity_prefers_api_key_over_ip() -> None:
    class _FakeRequest:
        headers = {"X-API-Key": "some-key"}
        client = None

    assert _client_identity(_FakeRequest()) == "key:some-key"  # type: ignore[arg-type]
