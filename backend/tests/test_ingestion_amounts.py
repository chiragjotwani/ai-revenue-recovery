"""Monetary-amount validation regression tests (BUG-002, BUG-003).

Before Phase 4.1:
  * an amount larger than ``NUMERIC(12, 2)`` could hold reached Postgres and
    produced an unhandled ``NumericValueOutOfRange`` -> HTTP 500 (BUG-002);
  * an amount with more than 2 fractional digits (e.g. ``1.999``) was
    accepted and silently rounded to ``2.00`` on persistence (BUG-003).

The API contract is now: amount in ``(0, 9_999_999_999.99]`` with at most
2 decimal places; anything else is ``422`` and nothing is stored.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment


def _payload(amount: str, *, ref: str = "amt-1", key: str = "amt-1") -> dict:
    return {
        "idempotency_key": key,
        "event_type": "payment.failed",
        "source": "test-suite",
        "occurred_at": "2026-08-27T10:00:00Z",
        "customer": {"external_id": "cust-amt", "email": "amt@example.com"},
        "payment": {
            "external_reference": ref,
            "amount": amount,
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }


@pytest.mark.parametrize(
    "amount",
    [
        "0.01",  # minimum sensible
        "1.99",
        "4999.00",
        "12345678.99",
        "9999999999.99",  # NUMERIC(12,2) maximum
        "100",  # integer, no decimal point
        "100.5",  # one decimal place
    ],
)
async def test_valid_amounts_are_accepted_and_stored_exactly(
    client: AsyncClient, db_session: AsyncSession, amount: str
) -> None:
    resp = await client.post("/events", json=_payload(amount))
    assert resp.status_code == 201, resp.text

    stored = await db_session.scalar(select(Payment.amount))
    assert stored == Decimal(amount), f"{amount!r} was stored as {stored!r}"


@pytest.mark.parametrize(
    "amount",
    [
        "0",  # not > 0
        "0.00",
        "-5.00",
        "not-a-number",
        "1.999",  # BUG-003: 3 dp, previously rounded to 2.00
        "1.9999",  # 4 dp
        "0.001",  # BUG-003: previously stored as 0.00
        "10000000000.00",  # BUG-002: one over the NUMERIC(12,2) max
        "123456789012.99",  # BUG-002: 12 integer digits
        "99999999999999.00",  # BUG-002: far over range, previously HTTP 500
    ],
)
async def test_invalid_amounts_are_rejected_with_422_and_persist_nothing(
    client: AsyncClient, db_session: AsyncSession, amount: str
) -> None:
    resp = await client.post("/events", json=_payload(amount))
    assert resp.status_code == 422, f"{amount!r} -> {resp.status_code}: {resp.text}"

    count = await db_session.scalar(select(func.count()).select_from(Payment))
    assert count == 0, f"{amount!r} must not create a payment row"


async def test_oversized_amount_never_returns_500(client: AsyncClient) -> None:
    # BUG-002 direct regression: the specific value from the audit.
    resp = await client.post("/events", json=_payload("99999999999999.00"))
    assert resp.status_code == 422
    assert resp.status_code != 500
