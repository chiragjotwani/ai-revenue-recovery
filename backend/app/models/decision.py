import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.diagnosis import Diagnosis
    from app.models.recovery import RecoveryCase


class DecisionResult(Base):
    """A stored, policy-produced Phase 5 decision for one (case, diagnosis).

    ``recoverability`` / ``candidate_strategy`` / ``approved_strategy`` /
    ``decision_status`` are stored as strings, not Postgres enums, for the
    same reason ``Diagnosis`` is (ADR-005): the taxonomy is app-validated
    (``app/decision/schema.py``) and expected to evolve without a migration.

    ``rationale`` stores the structured, typed rule outcomes
    (``DecisionRationaleEntry`` list) as JSON -- never AI free-text
    reasoning (see ADR-006 / the Phase 5 AI Trust Boundary).

    The unique constraint on ``(case_id, diagnosis_id)`` is the sole
    authoritative idempotency mechanism (KI-008): a decision is a function
    of exactly one case deciding against exactly one diagnosis, and the
    database -- not an application-level pre-check -- is what prevents a
    duplicate.
    """

    __tablename__ = "decision_results"
    __table_args__ = (
        UniqueConstraint("case_id", "diagnosis_id", name="uq_decision_results_case_diagnosis"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diagnoses.id"), nullable=False, index=True
    )

    recoverability: Mapped[str] = mapped_column(String(50), nullable=False)
    candidate_strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    approved_strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    decision_status: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    scheduled_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_engine_version: Mapped[str] = mapped_column(String(10), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped["RecoveryCase"] = relationship()
    diagnosis: Mapped["Diagnosis"] = relationship()
