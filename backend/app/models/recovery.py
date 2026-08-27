import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.payment import Payment


class RecoveryCaseState(str, enum.Enum):
    """Lifecycle states for a recovery case (Section 16).

    The full lifecycle is defined here even though Phase 3 only drives the
    earliest transitions: later phases (diagnosis, decision, action,
    observation) move a case further along the same machine without needing
    an enum migration. Legal transitions between these states are defined in
    ``app/recovery/state_machine.py`` -- state is never assigned directly.
    """

    DETECTED = "detected"
    DIAGNOSING = "diagnosing"
    DIAGNOSED = "diagnosed"
    DECISION_PENDING = "decision_pending"
    ACTION_SCHEDULED = "action_scheduled"
    ACTION_EXECUTED = "action_executed"
    OBSERVING = "observing"
    RECOVERED = "recovered"
    ABANDONED = "abandoned"
    FAILED = "failed"


# One shared Enum type object, reused by every column below so the Postgres
# ``recovery_case_state`` type is created exactly once. ``create_type`` is
# left at its default: the (single) migration that introduces this type is
# responsible for it, and Alembic is configured to emit it once.
_RECOVERY_CASE_STATE = SAEnum(RecoveryCaseState, name="recovery_case_state")


class RecoveryCase(Base):
    """An open investigation into a single at-risk payment.

    Exactly one case exists per payment attempt (``payment_id`` is unique):
    opening a case for a payment that already has one returns the existing
    case rather than creating a duplicate. ``state`` is only ever changed
    through the transition service, which records every change in
    ``recovery_case_transitions``.
    """

    __tablename__ = "recovery_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), unique=True, nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True
    )
    state: Mapped[RecoveryCaseState] = mapped_column(
        _RECOVERY_CASE_STATE, nullable=False, index=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    payment: Mapped["Payment"] = relationship()
    customer: Mapped["Customer"] = relationship()
    transitions: Mapped[list["RecoveryCaseTransition"]] = relationship(
        back_populates="case",
        order_by="RecoveryCaseTransition.created_at, RecoveryCaseTransition.id",
    )


class RecoveryCaseTransition(Base):
    """Append-only record of one state change on a recovery case.

    Rows are never updated or deleted by application code (Section 15 / 16:
    state transitions must be explicit and traceable). ``from_state`` is
    NULL only for the initial row that records a case entering
    ``DETECTED``. ``actor`` identifies what caused the transition, e.g.
    ``"system:open"`` or ``"api"``.
    """

    __tablename__ = "recovery_case_transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    from_state: Mapped[RecoveryCaseState | None] = mapped_column(
        _RECOVERY_CASE_STATE, nullable=True
    )
    to_state: Mapped[RecoveryCaseState] = mapped_column(_RECOVERY_CASE_STATE, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped["RecoveryCase"] = relationship(back_populates="transitions")
