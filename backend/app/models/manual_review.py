"""Human-in-the-loop manual review resolution (Phase 17: Advanced
Autonomous Recovery, human-in-the-loop scope).

A recovery case whose approved decision strategy is ``manual_review``
blocks in ``RecoveryCaseState.PENDING_MANUAL_REVIEW`` (see
``app.decision.actions.execute_action``) until an operator resolves it.
``ManualReviewResolution`` is the append-only audit record of that
resolution -- mirrors every other decision-point audit trail in this
system (``RecoveryCaseTransition``, ``RecoveryActionExecution``,
``RecoveryOutcomeObservation``): never updated or deleted, one row per
case (enforced by a unique constraint, since a case can only leave
``PENDING_MANUAL_REVIEW`` once -- the state machine has no edge back into
it).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ManualReviewOutcome(str, enum.Enum):
    """The two terminal states an operator may resolve a
    ``pending_manual_review`` case to -- deliberately not a full
    re-decision loop (would reopen decision/action identity questions
    out of this phase's scope) and never ``RECOVERED`` (no authoritative
    payment evidence exists merely because a human looked at the case;
    that claim can only ever come from Phase 7's evidence-based
    ``observe_outcome``, which this resolution does not invoke).
    """

    ABANDONED = "abandoned"
    FAILED = "failed"


class ManualReviewResolution(Base):
    """One operator's resolution of a case's manual-review escalation.

    ``case_id`` is unique: a case leaves ``PENDING_MANUAL_REVIEW`` at
    most once (the state machine has no edge back into it), so a second
    resolution attempt is a genuine conflict, not a replay to make
    idempotent (unlike every other append-only table in this system,
    which expects legitimate repeat attempts).
    """

    __tablename__ = "manual_review_resolutions"
    __table_args__ = (UniqueConstraint("case_id", name="uq_manual_review_resolutions_case_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    resolution: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["ManualReviewOutcome", "ManualReviewResolution"]
