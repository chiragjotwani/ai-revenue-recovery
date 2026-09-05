"""Integration tests for POST /recovery/cases/{id}/diagnose."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context_builder import RecoveryContext
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.base import RawModelResponse, ReasoningModel
from app.models.diagnosis import Diagnosis as DiagnosisRow

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-d",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": BASE.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "d@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201
    return r.json()


async def _ingest(client: AsyncClient, *, external_reference: str, **kw: object) -> dict:
    """Seed the canonical scenario for one customer: 3 successes then the
    failed payment. Returns the ingestion result for the failed payment.
    """
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            event_type="payment.succeeded",
            failure_reason=None,
        )
    return await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        failure_reason=str(kw.get("failure_reason", "insufficient_funds")),
    )


async def _open_case(client: AsyncClient, payment_id: str) -> str:
    r = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert r.status_code == 201
    return r.json()["id"]


async def test_diagnose_advances_case_and_stores_diagnosis(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payment = await _ingest(client, external_reference="p1")
    case_id = await _open_case(client, payment["payment_id"])

    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "insufficient_funds"
    assert body["disposition"] == "retriable_transient"
    assert body["recommended_strategy"] == "retry"
    assert body["recommended_delay_hours"] == 6
    assert body["prompt_version"] == DIAGNOSIS_PROMPT_VERSION
    assert body["model_name"] == "mock"
    assert 0.0 <= body["confidence"] <= 1.0

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "diagnosed"
    assert detail["diagnosis"]["outcome"] == "insufficient_funds"
    assert [h["to_state"] for h in detail["history"]] == ["detected", "diagnosing", "diagnosed"]

    # The payment itself is untouched -- the AI path never writes payments.
    payment_count = await db_session.scalar(select(func.count()).select_from(DiagnosisRow))
    assert payment_count == 1


async def test_diagnose_unknown_case_is_404(client: AsyncClient) -> None:
    r = await client.post(f"/recovery/cases/{uuid.uuid4()}/diagnose")
    assert r.status_code == 404


async def test_diagnose_twice_is_409_once_diagnosed(client: AsyncClient) -> None:
    payment = await _ingest(client, external_reference="p1")
    case_id = await _open_case(client, payment["payment_id"])

    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    second = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert second.status_code == 409


class _BadProvider(ReasoningModel):
    name = "bad"

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        return RawModelResponse(
            text="I refuse to answer in JSON.",
            model_name="bad",
            model_version="0",
            prompt_version=prompt_version,
            latency_ms=1,
        )


async def test_unvalidatable_model_output_is_502_and_leaves_case_in_diagnosing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.diagnosis.get_reasoning_model", lambda: _BadProvider())

    payment = await _ingest(client, external_reference="p1")
    case_id = await _open_case(client, payment["payment_id"])

    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 502

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "diagnosing"  # started but not completed
    assert detail["diagnosis"] is None

    rows = await db_session.scalar(select(func.count()).select_from(DiagnosisRow))
    assert rows == 0


async def test_diagnose_can_be_retried_from_diagnosing_after_a_failure(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.diagnosis.get_reasoning_model", lambda: _BadProvider())
    payment = await _ingest(client, external_reference="p1")
    case_id = await _open_case(client, payment["payment_id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 502

    # Restore a working provider and retry: the case is in DIAGNOSING now.
    monkeypatch.undo()
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200
    assert (await client.get(f"/recovery/cases/{case_id}")).json()["state"] == "diagnosed"
