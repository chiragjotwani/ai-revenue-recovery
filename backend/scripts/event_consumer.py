"""Consumer runner: drains Kafka into the audit projector (Phase 12,
ADR-007).

Usage:
    python scripts/event_consumer.py

Reads ``settings.kafka_domain_events_topic`` as consumer group
``settings.kafka_consumer_group``, parses each message back into a
``DomainEvent``, and hands it to ``app.events.consumer.process_event``
with ``app.events.handlers.EventAuditProjector`` -- which owns
idempotency (``processed_events``), bounded retry
(``settings.event_consumer_max_attempts``), and dead-lettering
(``dead_letter_events``) per event. The Kafka offset is committed only
after ``process_event`` returns (handled, duplicate, or dead-lettered)
-- never before -- so a crash mid-processing simply causes redelivery,
which idempotent processing absorbs rather than duplicating any effect.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.heartbeat import run_heartbeat_loop
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal
from app.events.consumer import process_event
from app.events.handlers import EventAuditProjector
from app.events.kafka import deserialize, kafka_consumer
from app.events.schema import DomainEvent

logger = logging.getLogger("app.events.consumer_runner")

_handler = EventAuditProjector()


async def run_consumer_forever() -> None:
    settings = get_settings()
    logger.info("event consumer starting: group=%s", settings.kafka_consumer_group)
    heartbeat_task = asyncio.create_task(run_heartbeat_loop())
    try:
        async with kafka_consumer(
            topic=settings.kafka_domain_events_topic,
            group_id=settings.kafka_consumer_group,
            bootstrap_servers=settings.kafka_bootstrap_servers,
        ) as consumer:
            async for message in consumer:
                raw = deserialize(message.value)
                event = DomainEvent.model_validate(raw)
                async with AsyncSessionLocal() as session:
                    result = await process_event(
                        session,
                        _handler,
                        event,
                        max_attempts=settings.event_consumer_max_attempts,
                    )
                logger.info(
                    "consumed event",
                    extra={
                        "event_id": str(event.event_id),
                        "event_type": event.event_type,
                        "aggregate_id": str(event.aggregate_id),
                        "correlation_id": str(event.resolved_correlation_id()),
                        "outcome": result.outcome.value,
                        "attempts": result.attempts,
                    },
                )
                await consumer.commit()
    finally:
        heartbeat_task.cancel()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_consumer_forever())
