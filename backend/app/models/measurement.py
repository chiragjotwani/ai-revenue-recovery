"""Revenue measurement model (Phase 8: Recovered Revenue Measurement).

``RevenueMeasurement`` is the idempotent, auditable record that a case's
current Phase 7 outcome observation was formally measured. It stores no
monetary figure of its own -- ``payment_id`` references the case's
original at-risk ``Payment`` row, the sole monetary source of truth
(amount/currency are always read from there, never duplicated here, so
nothing this table stores can drift from or override the payment record).

Identity mirrors the established pattern: one measurement per
``(case_id, outcome_observation_id)`` -- a measurement is a function of
exactly one case's exactly one (attempt-numbered) Phase 7 observation, the
same way ``DecisionResult`` is a function of exactly one
``(case_id, diagnosis_id)``. A case whose outcome later changes (e.g.
``unresolved`` -> ``recovered`` as new evidence arrives -- Phase 7's own
append-only design) gets a legitimately NEW measurement tied to the new
observation, not a mutation of the old one; the current, authoritative
measurement for a case is the one tied to its current (latest) outcome
observation, exactly as ``get_action_for_case``/``get_outcome_for_case``
already resolve "current" by joining through the case's current decision.

``status`` reuses ``app.outcome.schema.ObservedOutcome`` -- Phase 8
introduces no new recovered/not-recovered/unresolved vocabulary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.outcome import RecoveryOutcomeObservation
    from app.models.payment import Payment
    from app.models.recovery import RecoveryCase


class RevenueMeasurement(Base):
    """One idempotent measurement of a case's Phase 7 outcome.

    Never stores an amount or currency -- both are always read from the
    referenced ``Payment`` row (``payment_id``), so no measurement caller
    can supply, inflate, or alter a monetary figure (Phase 8 security
    requirement: the payment record remains the sole monetary source of
    truth).
    """

    __tablename__ = "revenue_measurements"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "outcome_observation_id", name="uq_revenue_measurements_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False, index=True
    )
    outcome_observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recovery_outcome_observations.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[RecoveryCase] = relationship()
    payment: Mapped[Payment] = relationship()
    outcome_observation: Mapped[RecoveryOutcomeObservation] = relationship()
