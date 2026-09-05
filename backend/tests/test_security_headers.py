"""Security response headers and CORS policy (Phase 15: Security &
Fintech Hardening).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_security_headers_present_on_every_response(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


async def test_hsts_header_absent_outside_production(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_header_present_in_production() -> None:
    from app.core.security_headers import _BASE_HEADERS, _HSTS_HEADER

    assert set(_BASE_HEADERS) == {"X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"}
    assert "Strict-Transport-Security" in _HSTS_HEADER


def test_no_cors_middleware_when_no_origins_configured() -> None:
    from starlette.middleware.cors import CORSMiddleware

    from app.main import create_app

    app = create_app()
    assert not any(m.cls is CORSMiddleware for m in app.user_middleware)


def test_cors_middleware_added_when_origins_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.middleware.cors import CORSMiddleware

    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(cors_allowed_origins_raw="https://example.com"),
    )
    app = create_app()
    assert any(m.cls is CORSMiddleware for m in app.user_middleware)
