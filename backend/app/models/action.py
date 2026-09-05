"""Recovery action & execution models (Phase 6: Action Executor).

Implements the identity/idempotency contract fixed in
``docs/recovery/action-idempotency.md`` before this phase existed:

* Action identity: ``(case_id, action_type, decision_result_id)`` -- what
  we intend to do, independent of attempts. A case has at most one action
  per decision (enforced by a database unique constraint, not a
  pre-check -- the KI-008 lesson).
* Execution identity: ``(action_id, attempt_no)`` -- one concrete attempt
  to carry it out. Also a database unique constraint.
* Idempotency key: ``f"arr:{case_id}:{action_type}:{attempt_no}"``, stored
  on the execution row.

``action_type`` reuses the existing ``RecoveryStrategy`` vocabulary
(``app/ai/schema.py``) -- Phase 6 introduces no new action taxonomy. It is
always exactly the decision's ``approved_strategy``, never the AI's raw
``candidate_strategy`` (ADR-003 / the AI trust boundary).

Stored as strings, not Postgres enums, for the same reason ``Diagnosis``
and ``DecisionResult`` are (ADR-005): the taxonomy is app-validated and
expected to evolve without a migration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.decision import DecisionResult
    from app.models.payment import Payment
    from app.models.recovery import RecoveryCase


class RecoveryActionStatus(str, enum.Enum):
    """Lifecycle of one scheduled action (not an attempt -- see
    ``RecoveryActionExecution`` for attempts).
    """

    SCHEDULED = "scheduled"
    EXECUTED = "executed"
    FAILED = "failed"


class ActionExecutionOutcome(str, enum.Enum):
    """What actually happened on one execution attempt.

    No REAL customer-messaging or payment-link integration exists anywhere
    in this repository -- ``request_payment_method_update`` and
    ``contact_customer`` always produce a ``SIMULATED_*`` value. ``retry``
    can produce either: ``SIMULATED_*`` when
    ``app.decision.providers_stripe.select_retry_provider`` fell back to
    the simulator (``Settings.stripe_api_key`` unset), or ``REAL_*`` when
    a genuine Stripe TEST-mode ``PaymentIntent`` call was made (Phase 16).
    Every execution row honestly records which of these happened, the same
    "explicit and observable substitution" discipline Phase 4's provider
    factory uses for its own mock fallback (KI-009) -- a caller can always
    tell simulated apart from real by this field alone, never by
    inspecting ``simulated_reference``'s string shape.

    * ``NO_SIDE_EFFECT_REQUIRED`` -- the approved strategy (``no_action``,
      or ``manual_review``) never needed an external system call. This is
      a genuine, safe completion, not a deferral.
    * ``SIMULATED_SUCCESS`` -- the deterministic simulated provider
      (``app.decision.providers.SimulatedPaymentProvider``) reported the
      attempt succeeded. ``RecoveryActionExecution.resulting_payment_id``
      is set to the new, simulated ``payment.succeeded`` evidence this
      created -- never claimed as a real external payment.
    * ``SIMULATED_TEMPORARY_FAILURE`` -- the simulated provider reported a
      transient failure. If the bounded retry cap has not been reached,
      the action remains ``scheduled`` and a further ``execute_action``
      call attempts the next attempt; otherwise the cap is exhausted (see
      ``RecoveryActionStatus.FAILED``).
    * ``SIMULATED_PERMANENT_FAILURE`` -- the simulated provider reported a
      non-retriable failure. No further attempts are made.
    * ``REAL_SUCCESS`` -- a genuine Stripe TEST-mode ``PaymentIntent``
      reached ``status="succeeded"``. Still TEST mode, never real money --
      but a real Stripe-side object and API round trip, not this
      repository's own deterministic simulation. Same
      ``resulting_payment_id`` linkage as ``SIMULATED_SUCCESS``.
    * ``REAL_TEMPORARY_FAILURE`` / ``REAL_PERMANENT_FAILURE`` -- the same
      transient/non-retriable distinction as their ``SIMULATED_*``
      counterparts, decided by Stripe's own TEST-mode decline code
      (``app.decision.providers_stripe``), not by this repository's fixed
      profile table.
    * ``DEFERRED_NO_INTEGRATION`` -- retained only for historical rows
      created before the simulated layer existed (pre Phase-6-completion).
      No code path in this repository produces this value going forward.
    """

    NO_SIDE_EFFECT_REQUIRED = "no_side_effect_required"
    SIMULATED_SUCCESS = "simulated_success"
    SIMULATED_TEMPORARY_FAILURE = "simulated_temporary_failure"
    SIMULATED_PERMANENT_FAILURE = "simulated_permanent_failure"
    REAL_SUCCESS = "real_success"
    REAL_TEMPORARY_FAILURE = "real_temporary_failure"
    REAL_PERMANENT_FAILURE = "real_permanent_failure"
    DEFERRED_NO_INTEGRATION = "deferred_no_integration"


class RecoveryAction(Base):
    """A scheduled recovery action for one policy-approved decision.

    ``action_type`` is always the decision's own ``approved_strategy`` --
    never chosen independently and never derived from AI free text. The
    unique constraint on ``(case_id, action_type, decision_result_id)`` is
    the action-identity idempotency mechanism (the database, not an
    application pre-check, is authoritative -- KI-008).
    """

    __tablename__ = "recovery_actions"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "action_type",
            "decision_result_id",
            name="uq_recovery_actions_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    decision_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_results.id"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[RecoveryCase] = relationship()
    decision_result: Mapped[DecisionResult] = relationship()
    executions: Mapped[list[RecoveryActionExecution]] = relationship(
        back_populates="action",
        order_by="RecoveryActionExecution.attempt_no",
    )


class RecoveryActionExecution(Base):
    """Append-only record of one concrete attempt to carry out a
    ``RecoveryAction``. Rows are never updated or deleted by application
    code (same append-only philosophy as ``RecoveryCaseTransition``).

    The unique constraint on ``(action_id, attempt_no)`` is the execution-
    identity idempotency mechanism.
    """

    __tablename__ = "recovery_action_executions"
    __table_args__ = (
        UniqueConstraint("action_id", "attempt_no", name="uq_recovery_action_executions_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_actions.id"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)

    #: A human-readable, always-clearly-simulated explanation of what the
    #: simulated provider reported (see app.decision.providers). NULL for
    #: NO_SIDE_EFFECT_REQUIRED executions, which have nothing to explain.
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: The deterministic simulated provider reference (always prefixed
    #: ``sim:`` -- see app.decision.providers.SimulatedPaymentProvider).
    #: Never formatted to resemble a real payment-gateway reference.
    simulated_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Set only on a SIMULATED_SUCCESS outcome: the id of the new,
    #: simulated ``Payment`` row (status=succeeded) this execution
    #: attempt caused -- the causal-correlation anchor Phase 7's
    #: evidence-based observation and Phase 8's measurement both read
    #: (via the ordinary later-successful-payment correlation rule, not a
    #: new one). See docs/recovery/action-idempotency.md.
    resulting_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    action: Mapped[RecoveryAction] = relationship(back_populates="executions")
    resulting_payment: Mapped[Payment | None] = relationship()
