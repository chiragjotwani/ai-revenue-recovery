"""Idempotent, retried, dead-lettered event consumption (Phase 12,
ADR-007).

``EventHandler`` is the interface a consumer registers -- purely a read
projection (e.g. ``app.events.handlers.EventAuditProjector``), never a
trigger for a state-changing domain operation (see this module's own
docstring boundary and ADR-007's "no duplicate business effects"
requirement: a handler that only ever produces a read-side row cannot,
by construction, duplicate a business effect no matter how many times it
is redelivered).

``process_event`` is the one function the consumer runner
(``scripts/event_consumer.py``) calls per message. It is deliberately
NOT Kafka-specific -- it takes an already-parsed ``DomainEvent`` and a
plain ``AsyncSession``, so it is fully testable without a running broker
(see ``tests/test_event_consumer.py``).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schema import DomainEvent
from app.models.domain_event import DeadLetterEvent, ProcessedEvent

logger = logging.getLogger(__name__)


class EventHandler(ABC):
    """One projection of the domain event stream."""

    #: A stable identity for this handler's own idempotency/DLQ records --
    #: distinct handlers processing the SAME Kafka consumer group would
    #: otherwise collide on ProcessedEvent's uniqueness.
    consumer_group: str

    @abstractmethod
    async def handle(self, session: AsyncSession, event: DomainEvent) -> None:
        """Apply one event. May raise -- ``process_event`` owns retry/DLQ,
        this method does not need to catch anything itself. Must not
        call an AI provider, must not schedule/execute a recovery action,
        must not modify a decision -- see the module docstring boundary.
        """
        raise NotImplementedError


class EventOutcome(str, Enum):
    HANDLED = "handled"
    DUPLICATE = "duplicate"
    DEAD_LETTERED = "dead_lettered"


@dataclass(frozen=True)
class ProcessResult:
    outcome: EventOutcome
    attempts: int


async def _already_processed(session: AsyncSession, event_id: object, consumer_group: str) -> bool:
    existing = await session.scalar(
        select(ProcessedEvent)
        .where(ProcessedEvent.event_id == event_id)
        .where(ProcessedEvent.consumer_group == consumer_group)
    )
    return existing is not None


async def process_event(
    session: AsyncSession,
    handler: EventHandler,
    event: DomainEvent,
    *,
    max_attempts: int,
) -> ProcessResult:
    """Idempotently apply ``event`` via ``handler``, with bounded retry
    and dead-lettering on exhaustion.

    Idempotency is DB-authoritative (KI-008 discipline): the pre-check
    below is an optimization only; the actual guarantee is the unique
    constraint on ``(event_id, consumer_group)`` plus the
    flush/``IntegrityError``/rollback/recheck this function performs when
    two consumer instances race on the same event.
    """
    if await _already_processed(session, event.event_id, handler.consumer_group):
        return ProcessResult(EventOutcome.DUPLICATE, attempts=0)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            await handler.handle(session, event)
        except Exception as exc:  # noqa: BLE001 -- bounded-retry boundary; re-raised as DLQ below, never swallowed
            last_error = exc
            logger.warning(
                "event handler failed: group=%s event_id=%s attempt=%s/%s error=%s",
                handler.consumer_group,
                event.event_id,
                attempt,
                max_attempts,
                exc,
            )
            await session.rollback()
            continue

        marker = ProcessedEvent(event_id=event.event_id, consumer_group=handler.consumer_group)
        session.add(marker)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            # A concurrent consumer instance already handled this event
            # for this group -- same KI-008 recheck pattern as every
            # prior phase's service module.
            if await _already_processed(session, event.event_id, handler.consumer_group):
                return ProcessResult(EventOutcome.DUPLICATE, attempts=attempt)
            raise
        await session.commit()
        return ProcessResult(EventOutcome.HANDLED, attempts=attempt)

    # Every attempt failed -- dead-letter, never retry forever, never
    # silently drop.
    dlq_row = DeadLetterEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        aggregate_id=event.aggregate_id,
        consumer_group=handler.consumer_group,
        payload=event.payload,
        error=str(last_error),
        attempts=max_attempts,
    )
    session.add(dlq_row)
    await session.commit()
    return ProcessResult(EventOutcome.DEAD_LETTERED, attempts=max_attempts)
