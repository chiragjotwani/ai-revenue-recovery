from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.event import IngestionEvent
from app.models.payment import Payment


def _event_payload(
    idempotency_key: str = "evt-1",
    event_type: str = "payment.failed",
    external_reference: str = "pay-1",
) -> dict:
    return {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": "2026-08-27T10:00:00Z",
        "customer": {
            "external_id": "cust-abc",
            "email": "customer@example.com",
            "name": "Test Customer",
        },
        "payment": {
            "external_reference": external_reference,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }


async def test_ingest_new_payment_event_creates_records(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/events", json=_event_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False

    payment_count = await db_session.scalar(select(func.count()).select_from(Payment))
    assert payment_count == 1


async def test_ingest_duplicate_idempotency_key_does_not_duplicate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _event_payload()

    first = await client.post("/events", json=payload)
    second = await client.post("/events", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert first.json()["payment_id"] == second.json()["payment_id"]

    event_count = await db_session.scalar(select(func.count()).select_from(IngestionEvent))
    payment_count = await db_session.scalar(select(func.count()).select_from(Payment))
    assert event_count == 1
    assert payment_count == 1


async def test_ingest_conflicting_reference_with_new_idempotency_key_is_rejected(
    client: AsyncClient,
) -> None:
    await client.post("/events", json=_event_payload(idempotency_key="evt-1"))

    response = await client.post(
        "/events", json=_event_payload(idempotency_key="evt-2", external_reference="pay-1")
    )

    assert response.status_code == 409


async def test_ingest_invalid_payload_is_rejected(client: AsyncClient) -> None:
    payload = _event_payload()
    del payload["payment"]["amount"]

    response = await client.post("/events", json=payload)

    assert response.status_code == 422


async def test_ingest_non_positive_amount_is_rejected(client: AsyncClient) -> None:
    payload = _event_payload()
    payload["payment"]["amount"] = "0.00"

    response = await client.post("/events", json=payload)

    assert response.status_code == 422


async def test_ingest_reuses_existing_customer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.post(
        "/events", json=_event_payload(idempotency_key="evt-1", external_reference="pay-1")
    )
    await client.post(
        "/events", json=_event_payload(idempotency_key="evt-2", external_reference="pay-2")
    )

    customer_count = await db_session.scalar(select(func.count()).select_from(Customer))
    assert customer_count == 1
