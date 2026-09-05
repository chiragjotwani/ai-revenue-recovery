"""Concrete event handlers (Phase 12, ADR-007).

``EventAuditProjector`` is the one handler wired into
``scripts/event_consumer.py`` by default: a pure read-side observer that
logs a structured audit line per event. It never mutates a case,
decision, action, or outcome -- by construction it cannot duplicate a
business effect no matter how many times ``process_event`` redelivers an
event to it, which is what lets it be safely at-least-once without extra
guarding beyond the idempotency ``process_event`` already provides.

Deliberately does not log ``event.payload`` in full: the payload may
contain customer/payment-adjacent fields, and this projector's job is
audit trail (what happened, when, correlated to what), not payload
inspection -- see ADR-007 and the Phase 14 "do not log sensitive
customer/payment information" constraint this handler was written to
already satisfy.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.consumer import EventHandler
from app.events.schema import DomainEvent

logger = logging.getLogger("app.events.audit")


class EventAuditProjector(EventHandler):
    """Logs a structured audit line for every domain event. Read-only."""

    consumer_group = "arr-event-audit-projector"

    async def handle(self, session: AsyncSession, event: DomainEvent) -> None:
        del session  # no database writes -- audit is the structured log itself
        logger.info(
            "domain_event_audit",
            extra={
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "aggregate_id": str(event.aggregate_id),
                "aggregate_type": event.aggregate_type,
                "correlation_id": str(event.resolved_correlation_id()),
                "occurred_at": event.occurred_at.isoformat(),
                "schema_version": event.schema_version,
            },
        )
