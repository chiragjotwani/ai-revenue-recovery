"""Outbox, consumer-idempotency, and dead-letter models (Phase 12, ADR-007).

Three tables, one job each:

* ``DomainEventRow`` -- the outbox. A domain service writes one of these in
  the SAME transaction as the state change it describes
  (``app.events.publisher.OutboxEventPublisher``). ``published_at`` is
  ``NULL`` until the relay (``scripts/event_relay.py``) successfully hands
  it to Kafka -- append-only, never deleted, the same audit philosophy as
  ``RecoveryCaseTransition``.
* ``ProcessedEvent`` -- consumer-side idempotency. Unique on ``event_id``:
  the database, not an application pre-check, is what makes a redelivered
  event a no-op (KI-008 discipline, applied here to Kafka redelivery
  instead of a duplicate HTTP request).
* ``DeadLetterEvent`` -- an event whose handler failed
  ``event_consumer_max_attempts`` times. Never silently dropped, never
  retried forever -- parked here for inspection/replay.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DomainEventRow(Base):
    """The outbox: one row per emitted domain event."""

    __tablename__ = "domain_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: NULL until the relay has confirmed Kafka accepted this event.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedEvent(Base):
    """One row per (event, consumer group) successfully handled --
    authoritative idempotency for consumption, not an application-level
    pre-check.
    """

    __tablename__ = "processed_events"
    __table_args__ = (
        UniqueConstraint("event_id", "consumer_group", name="uq_processed_events_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    consumer_group: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeadLetterEvent(Base):
    """An event whose handler failed on every bounded retry attempt --
    parked for inspection/replay, never silently dropped.
    """

    __tablename__ = "dead_letter_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    consumer_group: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
