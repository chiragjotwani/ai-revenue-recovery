"""Phase 14: Production Observability tests.

Covers: request-id propagation/correlation (``app.core.middleware``),
structured JSON logging (``app.core.logging``), the ``/metrics``
exposition (``app.core.metrics`` / ``app.api.observability``), and the
Kafka-aware-but-non-blocking readiness check (``app.api.health``,
already exercised in ``tests/test_health.py``).

Real Postgres, real HTTP (project policy: no mocking the database).
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from httpx import AsyncClient

from app.core.logging import _JSONFormatter, request_id_var

# --- request-id propagation --------------------------------------------


async def test_response_echoes_a_generated_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-Id")
    assert request_id is not None
    uuid.UUID(request_id)  # must be a valid UUID, not an empty/garbage string


async def test_response_echoes_back_a_caller_supplied_request_id(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-Id": "test-fixed-id-123"})
    assert response.headers["X-Request-Id"] == "test-fixed-id-123"


async def test_request_completion_is_logged_with_the_route_template_and_status(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.http"):
        response = await client.get("/health", headers={"X-Request-Id": "log-corr-test-id"})
    assert response.status_code == 200

    matching = [
        r for r in caplog.records if r.name == "app.http" and getattr(r, "route", None) == "/health"
    ]
    assert matching, [r.__dict__ for r in caplog.records]
    assert matching[-1].status == 200


async def test_request_id_is_not_leaked_across_requests(client: AsyncClient) -> None:
    """The contextvar must be reset after each request -- a request that
    supplies no header must never accidentally inherit a previous
    request's id.
    """
    first = await client.get("/health", headers={"X-Request-Id": "leaky-id-should-not-persist"})
    assert first.headers["X-Request-Id"] == "leaky-id-should-not-persist"

    second = await client.get("/health")
    assert second.headers["X-Request-Id"] != "leaky-id-should-not-persist"


# --- structured JSON logging --------------------------------------------


def test_json_formatter_includes_extra_fields_and_no_pii_by_default() -> None:
    formatter = _JSONFormatter()
    record = logging.LogRecord(
        name="app.decision.service",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="case decided",
        args=(),
        exc_info=None,
    )
    record.case_id = "11111111-1111-1111-1111-111111111111"
    record.decision_status = "approved"

    rendered = json.loads(formatter.format(record))
    assert rendered["message"] == "case decided"
    assert rendered["level"] == "INFO"
    assert rendered["case_id"] == "11111111-1111-1111-1111-111111111111"
    assert rendered["decision_status"] == "approved"
    # No accidental customer PII field name -- this formatter renders
    # whatever a caller passes, so the guarantee lives in what callers
    # choose to log (see app.core.logging's module docstring), but the
    # formatter itself introduces nothing.
    assert "email" not in rendered
    assert "reasoning" not in rendered


def test_json_formatter_includes_the_current_request_id() -> None:
    formatter = _JSONFormatter()
    token = request_id_var.set("req-abc-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        rendered = json.loads(formatter.format(record))
    finally:
        request_id_var.reset(token)
    assert rendered["request_id"] == "req-abc-123"


def test_json_formatter_omits_request_id_outside_a_request() -> None:
    formatter = _JSONFormatter()
    assert request_id_var.get() is None
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    rendered = json.loads(formatter.format(record))
    assert "request_id" not in rendered


# --- /metrics exposition --------------------------------------------------


async def test_metrics_endpoint_exposes_prometheus_text_format(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "arr_http_requests_total" in body
    assert "arr_revenue_at_risk" in body
    assert "arr_observed_recovered_revenue" in body
    assert "arr_recovery_attempts_total" in body


async def test_metrics_endpoint_itself_is_counted(client: AsyncClient) -> None:
    await client.get("/health")
    response = await client.get("/metrics")
    body = response.text
    assert 'route="/health"' in body


# --- separation from domain logic ---------------------------------------


async def test_metrics_scrape_never_mutates_operational_data(client: AsyncClient) -> None:
    before = (await client.get("/health/ready")).json()
    await client.get("/metrics")
    await client.get("/metrics")
    after = (await client.get("/health/ready")).json()
    assert before == after
