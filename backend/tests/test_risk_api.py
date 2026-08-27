from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


async def _ingest(client: AsyncClient, **overrides: object) -> dict:
    payload = {
        "idempotency_key": overrides.get("idempotency_key", "evt-default"),
        "event_type": overrides.get("event_type", "payment.failed"),
        "source": "test-suite",
        "occurred_at": overrides.get("occurred_at", BASE_TIME).isoformat(),
        "customer": {
            "external_id": overrides.get("customer_external_id", "cust-1"),
            "email": "customer@example.com",
        },
        "payment": {
            "external_reference": overrides["external_reference"],
            "amount": str(overrides.get("amount", "100.00")),
            "currency": overrides.get("currency", "inr"),
            "failure_reason": overrides.get("failure_reason"),
        },
    }
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    return response.json()


async def test_failed_payment_with_no_history_appears_at_risk(client: AsyncClient) -> None:
    await _ingest(
        client,
        idempotency_key="e1",
        external_reference="p1",
        failure_reason="insufficient_funds",
    )

    response = await client.get("/risk/payments")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["external_reference"] == "p1"
    assert body[0]["consecutive_failures"] == 1
    assert body[0]["risk_level"] in {"low", "medium", "high"}


async def test_failure_superseded_by_later_success_is_not_at_risk(
    client: AsyncClient,
) -> None:
    await _ingest(
        client,
        idempotency_key="e1",
        external_reference="p1",
        event_type="payment.failed",
        occurred_at=BASE_TIME,
        failure_reason="insufficient_funds",
    )
    await _ingest(
        client,
        idempotency_key="e2",
        external_reference="p2",
        event_type="payment.succeeded",
        occurred_at=BASE_TIME + timedelta(hours=6),
    )

    response = await client.get("/risk/payments")
    assert response.status_code == 200
    assert response.json() == []


async def test_consecutive_failures_counted_since_last_success(client: AsyncClient) -> None:
    await _ingest(
        client,
        idempotency_key="e1",
        external_reference="p1",
        event_type="payment.succeeded",
        occurred_at=BASE_TIME,
    )
    await _ingest(
        client,
        idempotency_key="e2",
        external_reference="p2",
        event_type="payment.failed",
        occurred_at=BASE_TIME + timedelta(days=1),
        failure_reason="insufficient_funds",
    )
    await _ingest(
        client,
        idempotency_key="e3",
        external_reference="p3",
        event_type="payment.failed",
        occurred_at=BASE_TIME + timedelta(days=2),
        failure_reason="insufficient_funds",
    )

    response = await client.get("/risk/payments")
    body = response.json()
    latest = next(a for a in body if a["external_reference"] == "p3")
    assert latest["consecutive_failures"] == 2
    assert latest["historical_success_rate"] == 1 / 3


async def test_risk_summary_aggregates_amount_and_levels(client: AsyncClient) -> None:
    await _ingest(
        client,
        idempotency_key="e1",
        external_reference="p1",
        amount="4999.00",
        currency="inr",
        failure_reason="insufficient_funds",
    )
    await _ingest(
        client,
        idempotency_key="e2",
        external_reference="p2",
        customer_external_id="cust-2",
        amount="1000.00",
        currency="inr",
        failure_reason="fraud_suspected",
    )

    response = await client.get("/risk/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["at_risk_payment_count"] == 2
    assert body["currency_breakdown"]["INR"] == "5999.00"
    assert body["risk_level_breakdown"]["high"] == 1


async def test_risk_summary_empty_when_no_failures(client: AsyncClient) -> None:
    response = await client.get("/risk/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["at_risk_payment_count"] == 0
    assert body["revenue_at_risk"] == "0"
