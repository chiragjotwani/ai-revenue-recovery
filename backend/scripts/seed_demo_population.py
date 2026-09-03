"""Seed a deterministic, multi-customer demo population (buildathon
finalization) -- separate from ``scripts/seed_synthetic_data.py``'s single
canonical fixture, which existing tests/manual QA depend on and which this
script never touches.

Purpose: give the dashboard (revenue at risk, recovery candidates,
strategy distribution, recovered/unrecovered revenue, baseline
comparison) a realistic-looking, but fully deterministic and disclosed,
population to demonstrate against -- not a single hand-picked case.

Deterministic: a fixed ``random.Random(SEED)`` drives every choice
(customer profile, event count, amounts, failure reasons, timing). Running
this script twice against an empty database produces byte-identical
external_references/idempotency_keys, so it is safe to re-run (every event
is idempotent -- see app.services.ingestion) and reproducible for a demo
rehearsal.

Scale: deliberately modest (tens of customers, low hundreds of events),
not "thousands" -- large enough to make risk/recovery/strategy
distributions look like a real population, small enough to seed in well
under a minute over plain HTTP and to stay easy to reason about in a
demo. See docs/project-state.md for this sizing decision.

This script goes through the SAME two paths every other actor in this
system uses: `POST /events` for ingestion (no direct DB writes -- reuses
all existing validation/idempotency) and the ordinary recovery API
(diagnose/decide/schedule-action/execute-action/observe-outcome/measure)
for every genuinely at-risk payment it creates. Nothing here is a shortcut
around the real pipeline; the resulting "recovered" cases are exactly as
real (and exactly as SIMULATED, per app/decision/providers.py) as any
case a human operator would produce by clicking through the UI.

Usage (with the venv active, Postgres reachable, backend running):
    python scripts/seed_demo_population.py
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

API_BASE_URL = "http://localhost:8000"
SEED = 20260904
CUSTOMER_COUNT = 28
NOW = datetime(2026, 9, 4, tzinfo=UTC)

# The same DiagnosisOutcome-recognised failure reasons app/ai/providers/mock.py
# maps (see _REASON_TO_OUTCOME) -- this script invents no new taxonomy.
_RETRIABLE_REASONS = ["insufficient_funds", "do_not_honor", "processing_error"]
_CUSTOMER_ACTION_REASONS = ["card_expired", "card_not_supported", "authentication_required"]
_FRAUD_REASONS = ["stolen_card", "lost_card", "fraud_suspected"]


@dataclass
class CustomerProfile:
    """A named, weighted behavioral archetype -- not random noise. Each
    profile is a deliberate, disclosed distribution over event count,
    failure probability, and which failure reasons occur, so the
    resulting population has plausible, inspectable structure (a
    real-world recovery dataset is not uniform-random either).
    """

    name: str
    weight: int
    event_count_range: tuple[int, int]
    failure_probability: float
    failure_reason_pool: list[str] = field(default_factory=lambda: list(_RETRIABLE_REASONS))
    amount_range: tuple[int, int] = (499, 9999)
    currency: str = "inr"


PROFILES = [
    CustomerProfile(
        name="reliable",
        weight=10,
        event_count_range=(4, 9),
        failure_probability=0.08,
        failure_reason_pool=_RETRIABLE_REASONS,
    ),
    CustomerProfile(
        name="at_risk_recoverable",
        weight=8,
        event_count_range=(3, 7),
        failure_probability=0.45,
        failure_reason_pool=_RETRIABLE_REASONS,
    ),
    CustomerProfile(
        name="needs_customer_action",
        weight=5,
        event_count_range=(3, 6),
        failure_probability=0.4,
        failure_reason_pool=_CUSTOMER_ACTION_REASONS,
    ),
    CustomerProfile(
        name="chronic_failer",
        weight=3,
        event_count_range=(3, 5),
        failure_probability=0.75,
        failure_reason_pool=_RETRIABLE_REASONS + _CUSTOMER_ACTION_REASONS,
    ),
    CustomerProfile(
        name="fraud_flagged",
        weight=2,
        event_count_range=(2, 4),
        failure_probability=0.5,
        failure_reason_pool=_FRAUD_REASONS,
    ),
    CustomerProfile(
        name="high_value_usd",
        weight=2,
        event_count_range=(3, 5),
        failure_probability=0.35,
        failure_reason_pool=_RETRIABLE_REASONS,
        amount_range=(10000, 49999),
        currency="usd",
    ),
]


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _weighted_profile(rng: random.Random) -> CustomerProfile:
    return rng.choices(PROFILES, weights=[p.weight for p in PROFILES], k=1)[0]


@dataclass
class PlannedEvent:
    idempotency_key: str
    external_reference: str
    event_type: str
    occurred_at: datetime
    customer_external_id: str
    customer_email: str
    amount: Decimal
    currency: str
    failure_reason: str | None


def _build_customer_events(
    rng: random.Random, customer_index: int, profile: CustomerProfile
) -> list[PlannedEvent]:
    customer_external_id = f"demo-cust-{customer_index:03d}"
    customer_email = f"{customer_external_id}@demo-population.example.com"
    event_count = rng.randint(*profile.event_count_range)

    # Spread events over the last 45 days, oldest first, so later phases'
    # "later successful payment" correlation rule has real chronological
    # room to work with.
    days_ago = sorted(rng.sample(range(1, 46), k=min(event_count, 45)), reverse=True)

    events: list[PlannedEvent] = []
    for i, day_offset in enumerate(days_ago):
        occurred_at = NOW - timedelta(days=day_offset, hours=rng.randint(0, 23))
        is_failure = rng.random() < profile.failure_probability
        amount = Decimal(rng.randint(*profile.amount_range))
        ref = f"demo-{customer_external_id}-{i:02d}"
        if is_failure:
            failure_reason = rng.choice(profile.failure_reason_pool)
            events.append(
                PlannedEvent(
                    idempotency_key=ref,
                    external_reference=ref,
                    event_type="payment.failed",
                    occurred_at=occurred_at,
                    customer_external_id=customer_external_id,
                    customer_email=customer_email,
                    amount=amount,
                    currency=profile.currency,
                    failure_reason=failure_reason,
                )
            )
        else:
            events.append(
                PlannedEvent(
                    idempotency_key=ref,
                    external_reference=ref,
                    event_type="payment.succeeded",
                    occurred_at=occurred_at,
                    customer_external_id=customer_external_id,
                    customer_email=customer_email,
                    amount=amount,
                    currency=profile.currency,
                    failure_reason=None,
                )
            )
    return events


def build_plan() -> list[PlannedEvent]:
    rng = random.Random(SEED)
    plan: list[PlannedEvent] = []
    for customer_index in range(1, CUSTOMER_COUNT + 1):
        profile = _weighted_profile(rng)
        plan.extend(_build_customer_events(rng, customer_index, profile))
    return plan


async def _ingest_all(
    client: httpx.AsyncClient, plan: list[PlannedEvent]
) -> list[dict[str, object]]:
    results = []
    for event in plan:
        payload = {
            "idempotency_key": event.idempotency_key,
            "event_type": event.event_type,
            "source": "seed-demo-population",
            "occurred_at": _iso(event.occurred_at),
            "customer": {
                "external_id": event.customer_external_id,
                "email": event.customer_email,
            },
            "payment": {
                "external_reference": event.external_reference,
                "amount": str(event.amount),
                "currency": event.currency,
                "failure_reason": event.failure_reason,
            },
        }
        response = await client.post("/events", json=payload)
        response.raise_for_status()
        result = response.json()
        result["_event_type"] = event.event_type
        results.append(result)
    return results


async def _run_recovery_pipeline(client: httpx.AsyncClient, payment_id: str) -> str:
    """Open a case (if the payment is genuinely still at-risk) and drive
    it as far through the real recovery pipeline as its own decision
    allows -- exactly the sequence a human operator or the frontend
    issues, no shortcuts. Returns a short status string for the summary.
    """
    opened = await client.post("/recovery/cases", json={"payment_id": payment_id})
    if opened.status_code == 409:
        return "not_recoverable"
    opened.raise_for_status()
    case_id = opened.json()["id"]

    diagnosed = await client.post(f"/recovery/cases/{case_id}/diagnose")
    if diagnosed.status_code != 200:
        return f"diagnose_failed:{diagnosed.status_code}"

    decided = await client.post(f"/recovery/cases/{case_id}/decide")
    if decided.status_code != 200:
        return f"decide_failed:{decided.status_code}"
    decision_status = decided.json()["decision_status"]
    if decision_status != "approved":
        return f"decision:{decision_status}"

    scheduled = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    if scheduled.status_code != 200:
        return f"schedule_failed:{scheduled.status_code}"

    # Bounded retry loop: keep calling execute-action while the action
    # remains "scheduled" (a temporary simulated failure with attempts
    # left) -- the same real, capped loop app/decision/actions.py
    # implements, never an artificial extra retry mechanism.
    for _ in range(5):
        executed = await client.post(f"/recovery/cases/{case_id}/execute-action")
        if executed.status_code != 200:
            return f"execute_failed:{executed.status_code}"
        if executed.json()["status"] != "scheduled":
            break

    observed = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    if observed.status_code != 200:
        return f"observe_failed:{observed.status_code}"
    outcome = observed.json()["outcome"]

    if outcome == "recovered":
        await client.post(f"/recovery/cases/{case_id}/measure")

    return f"outcome:{outcome}"


async def seed() -> None:
    plan = build_plan()
    print(f"Planned {len(plan)} events across {CUSTOMER_COUNT} customers (seed={SEED}).")

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        ingested = await _ingest_all(client, plan)
        created = sum(1 for r in ingested if not r["duplicate"])
        duplicate = len(ingested) - created
        print(f"Ingested: {created} new, {duplicate} already existed (idempotent replay).")

        failed_payment_ids = [
            str(r["payment_id"]) for r in ingested if r["_event_type"] == "payment.failed"
        ]
        print(
            f"Driving the real recovery pipeline for {len(failed_payment_ids)} failed payments..."
        )

        outcomes: dict[str, int] = {}
        for i, payment_id in enumerate(failed_payment_ids, start=1):
            status = await _run_recovery_pipeline(client, payment_id)
            outcomes[status] = outcomes.get(status, 0) + 1
            if i % 10 == 0 or i == len(failed_payment_ids):
                print(f"  ...{i}/{len(failed_payment_ids)}")

        print("\nRecovery pipeline outcomes:")
        for status, count in sorted(outcomes.items()):
            print(f"  {status}: {count}")

        report = await client.get("/measurement/report")
        report.raise_for_status()
        print("\nGET /measurement/report:")
        print(report.json())


if __name__ == "__main__":
    asyncio.run(seed())
