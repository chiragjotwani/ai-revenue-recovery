"""Concurrency / race-recovery tests (Phase 4.1, Workstream E3).

The audit noted that the ``except IntegrityError`` recovery branches in
``ingest_payment_event`` and ``open_case`` -- the code that matters under
real concurrent load -- had no tests. These fire genuinely concurrent
requests (``asyncio.gather``, independent sessions) at the same unique
constraint and assert the loser recovers cleanly instead of 500-ing or
creating a duplicate.

If the environment ever cannot schedule these truly concurrently the
asserts still hold (they only require "exactly one create, no duplicates,
no error"), so there is no fake-concurrency risk -- but on a normal event
loop + Postgres they do race.

KI-008 (Phase 4.1 follow-up): a forensic investigation confirmed
``test_concurrent_identical_ingestion_creates_one_payment`` failed
intermittently (~10-40% of runs under pytest) because of a genuine
time-of-check-to-time-of-use race in ``ingest_payment_event``'s now-removed
standalone ``external_reference`` pre-check -- NOT in the
``except IntegrityError`` recovery block, which was confirmed safe. The
KI008-0x tests below pin the documented idempotency contract explicitly so
this cannot silently regress.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_db_session
from app.main import app
from app.models.event import IngestionEvent
from app.models.payment import Payment
from app.models.recovery import RecoveryCase
from tests.conftest import TEST_OPERATOR_API_KEY


def _new_client() -> AsyncClient:
    async def _override() -> AsyncSession:  # fresh session per request
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": TEST_OPERATOR_API_KEY},
    )


def _event(key: str, ref: str, etype: str = "payment.failed") -> dict:
    return {
        "idempotency_key": key,
        "event_type": etype,
        "source": "test",
        "occurred_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
        "customer": {"external_id": "cc-cust", "email": "cc@e.com"},
        "payment": {
            "external_reference": ref,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }


async def test_concurrent_identical_ingestion_creates_one_payment() -> None:
    """KI008-01: same idempotency key + same external_reference, concurrent.

    Both callers must see the documented idempotency contract satisfied:
    both succeed, exactly one is the fresh insert, both resolve to the same
    payment, and exactly one payment/event row is ever persisted.
    """
    async with _new_client() as c:
        r1, r2 = await asyncio.gather(
            c.post("/events", json=_event("cc-key", "cc-ref")),
            c.post("/events", json=_event("cc-key", "cc-ref")),
        )
    app.dependency_overrides.clear()

    assert {r1.status_code, r2.status_code} == {201}
    bodies = [r1.json(), r2.json()]
    assert sum(b["duplicate"] for b in bodies) == 1, "exactly one caller sees a fresh insert"
    assert bodies[0]["payment_id"] == bodies[1]["payment_id"]

    async with AsyncSessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Payment)) == 1
        assert await s.scalar(select(func.count()).select_from(IngestionEvent)) == 1


async def test_concurrent_identical_ingestion_stress() -> None:
    """KI008-04: repeat the KI008-01 race many times in one test process.

    A permanent regression guard for the timing window the forensic
    investigation used to reproduce KI-008 (a per-process pytest invocation
    reproduced it at ~10-40%; a single test run repeating the race
    in-process is a cheap standing check that does not depend on re-running
    pytest itself many times).
    """
    count = 25
    for i in range(count):
        async with _new_client() as c:
            r1, r2 = await asyncio.gather(
                c.post("/events", json=_event(f"stress-key-{i}", f"stress-ref-{i}")),
                c.post("/events", json=_event(f"stress-key-{i}", f"stress-ref-{i}")),
            )
        app.dependency_overrides.clear()

        assert {r1.status_code, r2.status_code} == {201}, f"iteration {i}"
        bodies = [r1.json(), r2.json()]
        assert sum(b["duplicate"] for b in bodies) == 1, f"iteration {i}"
        assert bodies[0]["payment_id"] == bodies[1]["payment_id"], f"iteration {i}"


async def test_concurrent_same_reference_different_keys_is_a_genuine_conflict() -> None:
    """KI008-02: different idempotency keys, same external_reference.

    Exactly one request may create the payment; the other must receive the
    existing 409 conflict behaviour -- never a duplicate hit, never a
    second payment, never a 500. Two independent successes for the same
    external_reference under different keys is architecturally impossible
    (the unique constraint on ``Payment.external_reference`` forbids two
    payment rows sharing a reference), so this must be exactly one 201 and
    one 409, not merely "not 500".
    """
    async with _new_client() as c:
        r1, r2 = await asyncio.gather(
            c.post("/events", json=_event("k-a", "same-ref")),
            c.post("/events", json=_event("k-b", "same-ref")),
        )
    app.dependency_overrides.clear()

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [201, 409], codes

    async with AsyncSessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Payment)) == 1


async def test_sequential_replay_same_key_and_reference_is_idempotent() -> None:
    """KI008-03: same idempotency key, same external_reference, sequential.

    A plain (non-concurrent) replay after the first request has committed
    must return the existing payment as a duplicate, never a conflict.
    """
    async with _new_client() as c:
        first = await c.post("/events", json=_event("seq-key", "seq-ref"))
        second = await c.post("/events", json=_event("seq-key", "seq-ref"))
    app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert first.json()["payment_id"] == second.json()["payment_id"]

    async with AsyncSessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Payment)) == 1
        assert await s.scalar(select(func.count()).select_from(IngestionEvent)) == 1


async def test_concurrent_open_case_for_same_payment_creates_one_case() -> None:
    async with _new_client() as c:
        pid = (await c.post("/events", json=_event("oc-key", "oc-ref"))).json()["payment_id"]
        r1, r2 = await asyncio.gather(
            c.post("/recovery/cases", json={"payment_id": pid}),
            c.post("/recovery/cases", json={"payment_id": pid}),
        )
    app.dependency_overrides.clear()

    assert 500 not in {r1.status_code, r2.status_code}
    assert {r1.status_code, r2.status_code} <= {200, 201}
    assert r1.json()["id"] == r2.json()["id"]

    async with AsyncSessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(RecoveryCase)) == 1
