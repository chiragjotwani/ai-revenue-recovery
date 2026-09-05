"""Phase 10: integration tests for the read-only model report API
(``GET /ai/providers``, ``GET /ai/model-report``).

Real Postgres, real HTTP (project policy: no mocking the database).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.ai.context_builder import RecoveryContext
from app.ai.providers.base import RawModelResponse, ReasoningModel, ReasoningModelError


async def _ingest_and_open(client: AsyncClient, *, external_reference: str) -> uuid.UUID:
    payload = {
        "idempotency_key": external_reference,
        "event_type": "payment.failed",
        "source": "test-suite",
        "occurred_at": datetime(2026, 3, 1, tzinfo=UTC).isoformat(),
        "customer": {"external_id": f"cust-{external_reference}", "email": "report@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201
    payment_id = r.json()["payment_id"]
    case_r = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert case_r.status_code == 201
    return uuid.UUID(case_r.json()["id"])


async def test_provider_status_reflects_the_default_mock_config(client: AsyncClient) -> None:
    r = await client.get("/ai/providers")
    assert r.status_code == 200
    body = r.json()
    assert body["requested_provider"] == "mock"
    assert body["resolved_provider"] == "mock"
    assert body["substituted"] is False
    assert body["substitution_reason"] is None


async def test_model_report_aggregates_real_recorded_diagnoses(client: AsyncClient) -> None:
    case_id = await _ingest_and_open(client, external_reference="mr1")
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200

    report = (await client.get("/ai/model-report")).json()
    mock_entry = next(e for e in report["by_model"] if e["model_name"] == "mock")
    assert mock_entry["diagnosis_count"] >= 1
    assert mock_entry["mean_latency_ms"] >= 0
    assert 0.0 <= mock_entry["mean_confidence"] <= 1.0


async def test_model_report_counts_router_escalations(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _UnreachableProvider(ReasoningModel):
        name = "unreachable"

        async def diagnose(
            self, context: RecoveryContext, *, prompt_version: str
        ) -> RawModelResponse:
            raise ReasoningModelError("connection refused")

    before = (await client.get("/ai/model-report")).json()
    before_count = next(
        (e["escalation_count"] for e in before["by_model"] if e["model_name"] == "mock"), 0
    )

    monkeypatch.setattr(
        "app.services.diagnosis.get_reasoning_model", lambda: _UnreachableProvider()
    )
    case_id = await _ingest_and_open(client, external_reference="mr2")
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200
    assert r.json()["router_escalated"] is True
    assert r.json()["router_escalation_reason"] is not None

    after = (await client.get("/ai/model-report")).json()
    after_count = next(
        e["escalation_count"] for e in after["by_model"] if e["model_name"] == "mock"
    )
    assert after_count == before_count + 1


async def test_model_report_never_includes_synthetic_evaluation_data(client: AsyncClient) -> None:
    """The report must reflect only real persisted diagnoses. The test DB
    is truncated before every test (see conftest.py), so with no case
    diagnosed yet, the report must be genuinely empty -- never a
    fabricated non-zero baseline borrowed from the synthetic evaluation
    set (KI-007).
    """
    report = (await client.get("/ai/model-report")).json()
    assert report["by_model"] == []
