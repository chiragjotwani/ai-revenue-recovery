"""Phase 12 event architecture tests: outbox writes, idempotent/retried/
dead-lettered consumption, correlation IDs, and Kafka-transport edges
that do not require a running broker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.consumer import EventHandler, EventOutcome, process_event
from app.events.handlers import EventAuditProjector
from app.events.kafka import (
    KafkaUnavailableError,
    deserialize,
    kafka_consumer,
    kafka_producer,
    serialize,
)
from app.events.publisher import outbox_publisher
from app.events.schema import DomainEvent
from app.models.domain_event import DeadLetterEvent, DomainEventRow, ProcessedEvent


async def _seed_failed_payment(client: AsyncClient, *, idempotency_key: str) -> str:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": "payment.failed",
        "source": "test",
        "occurred_at": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
        "customer": {"external_id": f"cust-{idempotency_key}", "email": "e@e.com"},
        "payment": {
            "external_reference": idempotency_key,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }
    response_body: dict[str, str] = (await client.post("/events", json=payload)).json()
    return response_body["payment_id"]


# ---------------------------------------------------------------------------
# DomainEvent schema
# ---------------------------------------------------------------------------


def test_correlation_id_defaults_to_aggregate_id() -> None:
    aggregate_id = uuid.uuid4()
    event = DomainEvent(event_type="x", aggregate_id=aggregate_id, aggregate_type="t")
    assert event.resolved_correlation_id() == aggregate_id


def test_correlation_id_explicit_is_preserved() -> None:
    aggregate_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    event = DomainEvent(
        event_type="x",
        aggregate_id=aggregate_id,
        aggregate_type="t",
        correlation_id=correlation_id,
    )
    assert event.resolved_correlation_id() == correlation_id


# ---------------------------------------------------------------------------
# Outbox: opening/transitioning a case writes an unpublished domain event
# ---------------------------------------------------------------------------


async def test_opening_a_case_writes_an_outbox_event(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payment_id = await _seed_failed_payment(client, idempotency_key="ev-open-1")
    case = (await client.post("/recovery/cases", json={"payment_id": payment_id})).json()

    rows = (
        await db_session.scalars(
            select(DomainEventRow).where(DomainEventRow.aggregate_id == uuid.UUID(case["id"]))
        )
    ).all()
    opened = [r for r in rows if r.event_type == "recovery_case.opened"]
    assert len(opened) == 1
    row = opened[0]
    assert row.aggregate_type == "recovery_case"
    assert row.published_at is None
    assert row.payload["payment_id"] == payment_id
    assert row.correlation_id == row.aggregate_id


async def test_transitioning_a_case_writes_an_outbox_event(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payment_id = await _seed_failed_payment(client, idempotency_key="ev-trans-1")
    case = (await client.post("/recovery/cases", json={"payment_id": payment_id})).json()
    await client.post(f"/recovery/cases/{case['id']}/transitions", json={"to_state": "diagnosing"})

    rows = (
        await db_session.scalars(
            select(DomainEventRow)
            .where(DomainEventRow.aggregate_id == uuid.UUID(case["id"]))
            .where(DomainEventRow.event_type == "recovery_case.transitioned")
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].payload["from_state"] == "detected"
    assert rows[0].payload["to_state"] == "diagnosing"


async def test_opening_a_case_twice_does_not_write_a_second_outbox_event(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payment_id = await _seed_failed_payment(client, idempotency_key="ev-open-2")
    first = (await client.post("/recovery/cases", json={"payment_id": payment_id})).json()
    second = (await client.post("/recovery/cases", json={"payment_id": payment_id})).json()
    assert first["id"] == second["id"]

    rows = (
        await db_session.scalars(
            select(DomainEventRow)
            .where(DomainEventRow.aggregate_id == uuid.UUID(first["id"]))
            .where(DomainEventRow.event_type == "recovery_case.opened")
        )
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Idempotent / retried / dead-lettered consumption
# ---------------------------------------------------------------------------


class _CountingHandler(EventHandler):
    consumer_group = "test-counting-handler"

    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, session: AsyncSession, event: DomainEvent) -> None:
        del session, event
        self.calls += 1


class _AlwaysFailsHandler(EventHandler):
    consumer_group = "test-always-fails-handler"

    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, session: AsyncSession, event: DomainEvent) -> None:
        del session, event
        self.calls += 1
        raise RuntimeError("handler always fails")


class _FailsOnceHandler(EventHandler):
    consumer_group = "test-fails-once-handler"

    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, session: AsyncSession, event: DomainEvent) -> None:
        del session
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient failure")


def _make_event(**overrides: object) -> DomainEvent:
    defaults: dict[str, object] = {
        "event_type": "test.event",
        "aggregate_id": uuid.uuid4(),
        "aggregate_type": "test_aggregate",
    }
    defaults.update(overrides)
    return DomainEvent(**defaults)  # type: ignore[arg-type]


async def test_process_event_handles_once_and_records_processed_marker(
    db_session: AsyncSession,
) -> None:
    handler = _CountingHandler()
    event = _make_event()

    result = await process_event(db_session, handler, event, max_attempts=3)

    assert result.outcome == EventOutcome.HANDLED
    assert result.attempts == 1
    assert handler.calls == 1
    marker = await db_session.scalar(
        select(ProcessedEvent)
        .where(ProcessedEvent.event_id == event.event_id)
        .where(ProcessedEvent.consumer_group == handler.consumer_group)
    )
    assert marker is not None


async def test_process_event_is_idempotent_on_redelivery(db_session: AsyncSession) -> None:
    handler = _CountingHandler()
    event = _make_event()

    first = await process_event(db_session, handler, event, max_attempts=3)
    second = await process_event(db_session, handler, event, max_attempts=3)

    assert first.outcome == EventOutcome.HANDLED
    assert second.outcome == EventOutcome.DUPLICATE
    # The handler's own business effect happened exactly once, even
    # though process_event was invoked twice -- this is the "no duplicate
    # business effects on redelivery" guarantee ADR-007 requires.
    assert handler.calls == 1


async def test_process_event_retries_transient_failure_then_succeeds(
    db_session: AsyncSession,
) -> None:
    handler = _FailsOnceHandler()
    event = _make_event()

    result = await process_event(db_session, handler, event, max_attempts=3)

    assert result.outcome == EventOutcome.HANDLED
    assert result.attempts == 2
    assert handler.calls == 2


async def test_process_event_dead_letters_after_exhausting_retries(
    db_session: AsyncSession,
) -> None:
    handler = _AlwaysFailsHandler()
    event = _make_event(event_type="test.always_fails")

    result = await process_event(db_session, handler, event, max_attempts=3)

    assert result.outcome == EventOutcome.DEAD_LETTERED
    assert result.attempts == 3
    assert handler.calls == 3

    dlq_row = await db_session.scalar(
        select(DeadLetterEvent).where(DeadLetterEvent.event_id == event.event_id)
    )
    assert dlq_row is not None
    assert dlq_row.attempts == 3
    assert dlq_row.consumer_group == handler.consumer_group
    assert "handler always fails" in dlq_row.error

    # A dead-lettered event is NOT marked processed -- it was never
    # successfully handled, so a manual replay after fixing the handler
    # must be able to reprocess it.
    marker = await db_session.scalar(
        select(ProcessedEvent)
        .where(ProcessedEvent.event_id == event.event_id)
        .where(ProcessedEvent.consumer_group == handler.consumer_group)
    )
    assert marker is None


async def test_event_audit_projector_handles_without_error(db_session: AsyncSession) -> None:
    handler = EventAuditProjector()
    event = _make_event(event_type="recovery_case.opened")

    result = await process_event(db_session, handler, event, max_attempts=3)

    assert result.outcome == EventOutcome.HANDLED


# ---------------------------------------------------------------------------
# Kafka transport layer (no broker required)
# ---------------------------------------------------------------------------


def test_serialize_deserialize_roundtrip_preserves_uuid_as_string() -> None:
    original = {"event_id": uuid.uuid4(), "count": 1, "nested": {"a": "b"}}
    raw = serialize(original)
    result = deserialize(raw)
    assert result["event_id"] == str(original["event_id"])
    assert result["count"] == 1
    assert result["nested"] == {"a": "b"}


async def test_kafka_producer_raises_when_unconfigured() -> None:
    with pytest.raises(KafkaUnavailableError):
        async with kafka_producer(None):
            pass  # pragma: no cover -- must raise before yielding


async def test_kafka_consumer_raises_when_unconfigured() -> None:
    with pytest.raises(KafkaUnavailableError):
        async with kafka_consumer(topic="t", group_id="g", bootstrap_servers=None):
            pass  # pragma: no cover -- must raise before yielding


# ---------------------------------------------------------------------------
# OutboxEventPublisher does not commit its own transaction
# ---------------------------------------------------------------------------


async def test_outbox_publisher_flushes_but_does_not_commit(db_session: AsyncSession) -> None:
    event = _make_event(event_type="test.uncommitted")
    await outbox_publisher.publish(db_session, event)

    # Visible within the same uncommitted transaction/session...
    row = await db_session.get(DomainEventRow, event.event_id)
    assert row is not None

    # ...but rolling back discards it entirely -- publish() never opens
    # or commits its own transaction, so it is only as durable as
    # whatever the caller does next.
    await db_session.rollback()
    row_after_rollback = await db_session.get(DomainEventRow, event.event_id)
    assert row_after_rollback is None
