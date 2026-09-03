"""Tests for the baseline-vs-AI recovery comparison (buildathon
finalization). Real Postgres, real HTTP (project policy: no mocking the
database) -- mirrors test_revenue_measurement.py's conventions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str,
    amount: str = "4999.00",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "bl@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": amount,
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _executed_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, failure_reason: str
) -> uuid.UUID:
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_external_id,
        )
    payment = await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        occurred_at=BASE,
        failure_reason=failure_reason,
        customer_external_id=customer_external_id,
    )
    case = await client.post("/recovery/cases", json={"payment_id": payment["payment_id"]})
    case_id = uuid.UUID(case.json()["id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/decide")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/schedule-action")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/execute-action")).status_code == 200
    await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    return case_id


async def test_methodology_is_disclosed_and_never_claims_causality(client: AsyncClient) -> None:
    r = await client.get("/measurement/baseline-comparison")
    assert r.status_code == 200
    body = r.json()
    assert body["counterfactual_available"] is False
    methodology = body["methodology"].lower()
    assert "not a randomized control" in methodology
    assert "not a causal" in methodology
    assert "blind retry" in methodology


async def test_ai_gated_success_case_counts_as_ai_recovered_and_baseline_would_also_recover(
    client: AsyncClient,
) -> None:
    """insufficient_funds succeeds under both the real AI-gated pipeline
    (attempt 1) and the simulated baseline (same profile, same channel).
    """
    await _executed_case(
        client,
        external_reference="bl1",
        customer_external_id="cust-bl1",
        failure_reason="insufficient_funds",
    )

    r = await client.get("/measurement/baseline-comparison")
    body = r.json()
    assert body["eligible_case_count"] >= 1
    ai_inr = next(
        (row for row in body["ai_gated_observed_recovered"] if row["currency"] == "INR"), None
    )
    baseline_inr = next(
        (row for row in body["baseline_simulated_recovered"] if row["currency"] == "INR"), None
    )
    assert ai_inr is not None and ai_inr["case_count"] >= 1
    assert baseline_inr is not None and baseline_inr["case_count"] >= 1


async def test_fraud_case_is_counted_as_a_gate_that_avoided_a_blind_retry(
    client: AsyncClient,
) -> None:
    """A fraud-flagged case escalates to manual_review under the real
    policy engine (unmodified) -- a blind-retry baseline has no such
    safety check and would have retried it anyway. Escalated decisions
    are never schedulable/executable (ADR-003), so this stops at decide.
    """
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"bl2-s{i}",
            external_reference=f"bl2-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id="cust-bl2",
        )
    payment = await _ingest_one(
        client,
        idempotency_key="bl2-f",
        external_reference="bl2-f",
        occurred_at=BASE,
        failure_reason="fraud_suspected",
        customer_external_id="cust-bl2",
    )
    case = await client.post("/recovery/cases", json={"payment_id": payment["payment_id"]})
    case_id = uuid.UUID(case.json()["id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    decided = await client.post(f"/recovery/cases/{case_id}/decide")
    assert decided.status_code == 200
    assert decided.json()["decision_status"] == "escalated"

    # An escalated decision is never schedulable -- confirms this case
    # never reaches _eligible_cases through a real action, only through
    # having a DecisionResult at all (measurement.service._eligible_cases'
    # own eligibility rule: any case with a decision, per its docstring).
    r = await client.get("/measurement/baseline-comparison")
    body = r.json()
    assert body["cases_where_ai_gate_avoided_a_blind_retry"] >= 1
