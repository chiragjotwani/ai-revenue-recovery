"""Event publication: the interface domain code depends on (Phase 12,
ADR-007).

``EventPublisher`` is deliberately Kafka-agnostic -- no
``app.decision``/``app.outcome``/``app.measurement``/``app.recovery``
module ever imports ``app.events.kafka``. ``OutboxEventPublisher`` is the
only implementation domain services call: it writes the event to the
``domain_events`` outbox table in the CALLER's own session/transaction
(never opens a new one, never commits) -- the event becomes durable
exactly when, and only when, the state change it describes does. Actually
getting it to Kafka is the relay's job (``scripts/event_relay.py``), not
this module's.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schema import DomainEvent
from app.models.domain_event import DomainEventRow


class EventPublisher(ABC):
    @abstractmethod
    async def publish(self, session: AsyncSession, event: DomainEvent) -> None:
        """Durably record ``event`` for later delivery. Must be called
        inside the same transaction as the domain state change the event
        describes -- this method itself never commits.
        """
        raise NotImplementedError


class OutboxEventPublisher(EventPublisher):
    """Writes to the ``domain_events`` outbox table. No network call, no
    Kafka dependency -- publication never fails because Kafka is down;
    only the relay's later delivery step can.
    """

    async def publish(self, session: AsyncSession, event: DomainEvent) -> None:
        row = DomainEventRow(
            id=event.event_id,
            event_type=event.event_type,
            aggregate_id=event.aggregate_id,
            aggregate_type=event.aggregate_type,
            occurred_at=event.occurred_at,
            schema_version=event.schema_version,
            source=event.source,
            correlation_id=event.resolved_correlation_id(),
            payload=event.payload,
        )
        session.add(row)
        await session.flush()


#: The one publisher instance domain services use. A module-level
#: singleton (like every provider factory's default) rather than DI
#: plumbing through every service signature -- it holds no connection
#: state (no Kafka client), so sharing it is safe.
outbox_publisher = OutboxEventPublisher()
