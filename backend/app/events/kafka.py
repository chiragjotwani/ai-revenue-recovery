"""Kafka-specific transport (Phase 12, ADR-007).

Confined to this one module by design: only the relay
(``scripts/event_relay.py``) and the consumer runner
(``scripts/event_consumer.py``) import it. No domain service does --
``app.events.publisher.EventPublisher`` is the boundary they see instead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

logger = logging.getLogger(__name__)


def _json_default(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def serialize(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, default=_json_default).encode("utf-8")


def deserialize(raw: bytes) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return result


class KafkaUnavailableError(Exception):
    """Raised when ``KAFKA_BOOTSTRAP_SERVERS`` is unset -- the relay/
    consumer cannot start, but this NEVER affects the synchronous
    request-serving backend (ADR-007): the outbox still fills safely,
    it simply is not drained until a broker is configured/reachable.
    """


@asynccontextmanager
async def kafka_producer(bootstrap_servers: str | None) -> AsyncIterator[AIOKafkaProducer]:
    if not bootstrap_servers:
        raise KafkaUnavailableError("KAFKA_BOOTSTRAP_SERVERS is not configured")
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
    await producer.start()
    try:
        yield producer
    finally:
        await producer.stop()


@asynccontextmanager
async def kafka_consumer(
    *, topic: str, group_id: str, bootstrap_servers: str | None
) -> AsyncIterator[AIOKafkaConsumer]:
    if not bootstrap_servers:
        raise KafkaUnavailableError("KAFKA_BOOTSTRAP_SERVERS is not configured")
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        yield consumer
    finally:
        await consumer.stop()
