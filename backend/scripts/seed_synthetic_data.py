"""Seed synthetic data for local development and manual testing.

Produces the canonical end-to-end scenario from the engineering prompt
(Section 38): a customer with historically successful payments, then one
failed payment of 4999.00 due to insufficient funds. Later phases (risk
detection, recovery, diagnosis) are exercised against this dataset.

Usage (with the venv active and Postgres reachable):
    python scripts/seed_synthetic_data.py
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

API_BASE_URL = "http://localhost:8000"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def seed() -> None:
    now = datetime.now(UTC)
    customer = {
        "external_id": "cust-canonical-001",
        "email": "canonical.customer@example.com",
        "name": "Canonical Customer",
    }

    events = []
    # Three historically successful payments.
    for i in range(3):
        occurred_at = now - timedelta(days=30 * (3 - i))
        events.append(
            {
                "idempotency_key": f"seed-success-{i}",
                "event_type": "payment.succeeded",
                "source": "seed-script",
                "occurred_at": _iso(occurred_at),
                "customer": customer,
                "payment": {
                    "external_reference": f"seed-pay-success-{i}",
                    "amount": str(Decimal("4999.00")),
                    "currency": "inr",
                    "failure_reason": None,
                },
            }
        )

    # The canonical failing payment.
    events.append(
        {
            "idempotency_key": "seed-failure-0",
            "event_type": "payment.failed",
            "source": "seed-script",
            "occurred_at": _iso(now),
            "customer": customer,
            "payment": {
                "external_reference": "seed-pay-failure-0",
                "amount": str(Decimal("4999.00")),
                "currency": "inr",
                "failure_reason": "insufficient_funds",
            },
        }
    )

    async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
        for event in events:
            response = await client.post("/events", json=event)
            response.raise_for_status()
            result = response.json()
            status = "duplicate" if result["duplicate"] else "created"
            print(f"{event['idempotency_key']}: {status}")


if __name__ == "__main__":
    asyncio.run(seed())
