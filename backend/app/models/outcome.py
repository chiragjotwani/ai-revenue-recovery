"""Recovery outcome observation model (Phase 7: Outcome Observation).

Mirrors ``app.models.action``'s own shape deliberately, per the same
append-only-attempts convention ``RecoveryActionExecution`` already
established: ``RecoveryOutcomeObservation`` is one row per observation
*attempt* for one action (identified by ``(action_id, attempt_no)``,
a database unique constraint -- the same KI-008 discipline). The row with
the highest ``attempt_no`` for a given action is the current, authoritative
outcome; earlier rows are kept, never updated or deleted, as an audit
trail of how the classification evolved as evidence arrived over time
(exactly why this is NOT a single mutable "current outcome" row: an
``unresolved`` case can later become ``recovered`` as new evidence
arrives, and that history is itself worth keeping).

No new aggregate table is introduced -- unlike
``RecoveryAction``/``RecoveryActionExecution``, there is no separate
"current state" row to keep in sync; the append-only table alone is
sufficient and avoids a second source of truth.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.action import RecoveryAction
    from app.models.payment import Payment
    from app.models.recovery import RecoveryCase


class RecoveryOutcomeObservation(Base):
    """One observation attempt of a ``RecoveryAction``'s real-world
    outcome, classified exclusively from authoritative
    ``app.models.payment.Payment`` evidence -- never from AI reasoning,
    never from the mere fact that the action executed.

    ``outcome`` is stored as a string, not a Postgres enum, for the same
    reason ``Diagnosis``/``DecisionResult``/``RecoveryAction`` are
    (ADR-005). ``evidence_payment_id`` is nullable: an ``unresolved``
    observation has no evidence yet.
    """

    __tablename__ = "recovery_outcome_observations"
    __table_args__ = (
        UniqueConstraint(
            "action_id", "attempt_no", name="uq_recovery_outcome_observations_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_actions.id"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[RecoveryCase] = relationship()
    action: Mapped[RecoveryAction] = relationship()
    evidence_payment: Mapped[Payment | None] = relationship()
