"""Domain event contracts (Phase 12, ADR-007).

``DomainEvent`` is the one shape every event this platform emits uses,
regardless of which aggregate produced it. Domain services never
construct a raw dict for Kafka -- they build a ``DomainEvent`` and hand it
to ``app.events.publisher.EventPublisher``, which is the only thing that
knows how it gets persisted/transported.

No AI free-text (a diagnosis's ``reasoning``) ever enters ``payload`` --
the same AI trust boundary every prior phase's typed-fields-only inputs
already established. ``payload`` is a bounded, typed dict of already
-validated domain fields, never a raw ORM row or free-text blob.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

#: Bumped only if DomainEvent's own shape changes -- distinct from any
#: individual event_type's payload shape, mirrors the
#: decision_engine_version / feature_version precedent every prior phase
#: already uses for its own persisted artifacts.
EVENT_SCHEMA_VERSION = "1"

SOURCE = "ai-revenue-recovery-backend"


class DomainEvent(BaseModel):
    """One domain event, ready to be handed to an ``EventPublisher``.

    ``correlation_id`` defaults to ``aggregate_id`` when not supplied
    explicitly -- for this platform, a recovery case's own id is already
    the natural correlation key threading event -> case -> AI -> policy
    -> action -> outcome (Phase 14 tracing depends on this being
    consistent, not per-event-random).
    """

    model_config = {"extra": "forbid"}

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str = Field(min_length=1, max_length=100)
    aggregate_id: uuid.UUID
    aggregate_type: str = Field(min_length=1, max_length=50)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: str = EVENT_SCHEMA_VERSION
    source: str = SOURCE
    correlation_id: uuid.UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def resolved_correlation_id(self) -> uuid.UUID:
        return self.correlation_id or self.aggregate_id
