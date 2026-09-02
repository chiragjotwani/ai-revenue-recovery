"""Relay: drains the ``domain_events`` outbox to Kafka (Phase 12, ADR-007).

Usage:
    python scripts/event_relay.py

Polls for unpublished rows (``published_at IS NULL``, oldest first --
the partial index added by migration ``c2d8f6a91e53`` makes this cheap),
publishes each to ``settings.kafka_domain_events_topic`` keyed by
``aggregate_id`` (so all events for one aggregate land on the same
partition and are delivered in order to any one consumer), then marks
``published_at``. A row is only marked published after Kafka has
acknowledged the send -- a crash between send and mark simply causes a
duplicate publish on the next pass, which is exactly what the
consumer's idempotent processing (``app.events.consumer.process_event``)
exists to absorb.

This process is intentionally separate from the request-serving
backend: if Kafka is down, the outbox just grows and requests keep
succeeding (ADR-007) -- this script is the only thing that stalls.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.events.kafka import kafka_producer, serialize
from app.models.domain_event import DomainEventRow

logger = logging.getLogger("app.events.relay")

POLL_INTERVAL_SECONDS = 2.0
BATCH_SIZE = 100


async def _fetch_unpublished_batch() -> list[DomainEventRow]:
    async with AsyncSessionLocal() as session:
        result = await session.scalars(
            select(DomainEventRow)
            .where(DomainEventRow.published_at.is_(None))
            .order_by(DomainEventRow.created_at)
            .limit(BATCH_SIZE)
        )
        return list(result)


async def _mark_published(row_id: object) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(DomainEventRow, row_id)
        if row is not None:
            row.published_at = datetime.now(UTC)
            await session.commit()


async def run_relay_once() -> int:
    """Publish one batch of unpublished events. Returns the count sent."""
    settings = get_settings()
    rows = await _fetch_unpublished_batch()
    if not rows:
        return 0

    async with kafka_producer(settings.kafka_bootstrap_servers) as producer:
        for row in rows:
            payload = {
                "event_id": str(row.id),
                "event_type": row.event_type,
                "aggregate_id": str(row.aggregate_id),
                "aggregate_type": row.aggregate_type,
                "occurred_at": row.occurred_at.isoformat(),
                "schema_version": row.schema_version,
                "source": row.source,
                "correlation_id": str(row.correlation_id),
                "payload": row.payload,
            }
            await producer.send_and_wait(
                settings.kafka_domain_events_topic,
                value=serialize(payload),
                key=str(row.aggregate_id).encode("utf-8"),
            )
            await _mark_published(row.id)
            logger.info("relayed event_id=%s event_type=%s", row.id, row.event_type)

    return len(rows)


async def run_relay_forever() -> None:
    logger.info("event relay starting")
    while True:
        sent = await run_relay_once()
        if sent == 0:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_relay_forever())
