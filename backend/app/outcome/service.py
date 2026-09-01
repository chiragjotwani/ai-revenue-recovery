"""Outcome observation service (Phase 7): orchestration, persistence,
idempotency.

``observe_outcome`` is the single entry point. It never executes an action,
never calls an AI provider, never overrides a Phase 5 decision or a Phase 6
action -- it reads a case's current, executed ``RecoveryAction`` and
classifies what actually happened from authoritative
``app.models.payment.Payment`` evidence alone.

Scope boundary (mirrors ``app.decision.service`` and
``app.decision.actions``'s own docstrings): this module observes. It never
schedules or executes a recovery action, never re-runs the policy engine,
and never marks anything recovered merely because an action executed --
that would collapse exactly the distinction Phase 7 exists to preserve
(see the module docstring in ``app.outcome.schema``).

Correlation rule (the one this module uses, and the only one): a later
``payment.succeeded`` -- or ``payment.failed`` -- event for the SAME
customer, occurring after the originally failed payment's ``occurred_at``.
This is not a new rule invented for Phase 7: it is the exact same
deterministic relationship ``app.decision.service::_has_later_successful_payment``
(the ``already_paid`` check) and ``app.risk.service``'s at-risk-payment
exclusion already use. Phase 7 does not invent fuzzy matching, a time
window, or a monetary/confidence threshold.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import CaseNotObservableError, NoExecutedActionError
from app.decision.service import get_decision_for_case
from app.models.action import RecoveryAction, RecoveryActionStatus
from app.models.outcome import RecoveryOutcomeObservation
from app.models.payment import Payment, PaymentStatus
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.outcome.schema import ObservedOutcome, OutcomeClassification
from app.recovery import service as recovery_service

_OBSERVABLE_FROM = {RecoveryCaseState.ACTION_EXECUTED, RecoveryCaseState.OBSERVING}
_OBSERVE_ACTOR = "system:observe_outcome"


async def _get_action_for_case(session: AsyncSession, case_id: UUID) -> RecoveryAction | None:
    decision = await get_decision_for_case(session, case_id)
    if decision is None:
        return None
    action: RecoveryAction | None = await session.scalar(
        select(RecoveryAction)
        .where(RecoveryAction.case_id == case_id)
        .where(RecoveryAction.action_type == decision.approved_strategy)
        .where(RecoveryAction.decision_result_id == decision.id)
    )
    return action


async def _later_payment(
    session: AsyncSession, customer_id: UUID, after: object, status: PaymentStatus
) -> Payment | None:
    """A payment for ``customer_id`` with the given ``status``, occurring
    strictly after ``after`` (the originally failed payment's
    ``occurred_at``). The exact query shape
    ``app.decision.service::_has_later_successful_payment`` already uses,
    parameterized by status and returning the row (not just a boolean) so
    it can serve as this observation's evidence.
    """
    later = aliased(Payment)
    stmt = (
        select(later)
        .where(later.customer_id == customer_id)
        .where(later.status == status)
        .where(later.occurred_at > after)
        .order_by(later.occurred_at.asc())
        .limit(1)
    )
    result: Payment | None = await session.scalar(stmt)
    return result


async def classify_outcome(session: AsyncSession, case: RecoveryCase) -> OutcomeClassification:
    """Pure(-ish) classification: reads only ``Payment`` rows, never
    mutates anything. Exposed separately from :func:`observe_outcome` so
    the classification rule itself is unit-testable without exercising
    persistence/state-transition machinery.
    """
    failed_payment = await session.get(Payment, case.payment_id)
    assert failed_payment is not None  # guaranteed by RecoveryCase.payment_id's FK

    success = await _later_payment(
        session, case.customer_id, failed_payment.occurred_at, PaymentStatus.SUCCEEDED
    )
    if success is not None:
        return OutcomeClassification(
            outcome=ObservedOutcome.RECOVERED,
            is_terminal=True,
            evidence_payment_id=success.id,
        )

    failure = await _later_payment(
        session, case.customer_id, failed_payment.occurred_at, PaymentStatus.FAILED
    )
    if failure is not None:
        return OutcomeClassification(
            outcome=ObservedOutcome.NOT_RECOVERED,
            is_terminal=False,
            evidence_payment_id=failure.id,
        )

    return OutcomeClassification(outcome=ObservedOutcome.UNRESOLVED, is_terminal=False)


async def get_outcome_for_case(
    session: AsyncSession, case_id: UUID
) -> RecoveryOutcomeObservation | None:
    """The latest (highest ``attempt_no``) observation for a case's
    current action, if any has been recorded yet -- the read-only
    counterpart to :func:`observe_outcome`. Returns ``None`` for a case
    with no action yet, or one never observed, never raises for either.
    """
    action = await _get_action_for_case(session, case_id)
    if action is None:
        return None
    result: RecoveryOutcomeObservation | None = await session.scalar(
        select(RecoveryOutcomeObservation)
        .where(RecoveryOutcomeObservation.action_id == action.id)
        .order_by(
            RecoveryOutcomeObservation.attempt_no.desc(), RecoveryOutcomeObservation.id.desc()
        )
        .limit(1)
    )
    return result


async def observe_outcome(
    session: AsyncSession, case_id: UUID
) -> tuple[RecoveryCase, RecoveryOutcomeObservation, bool]:
    """Observe the real-world outcome of a case's executed action.

    Returns ``(case, observation, created)``. ``created=False`` when the
    freshly computed classification is identical (same outcome, same
    evidence) to the latest already-persisted observation -- a true
    idempotent replay, whether a sequential re-poll or a duplicate event
    re-ingested. New, different evidence always creates a new append-only
    attempt (mirrors ``RecoveryActionExecution``): an ``unresolved`` case
    genuinely becoming ``recovered`` later is not a duplicate, it is a new
    fact, and is kept as history rather than overwriting the prior row.

    Advances the case ``action_executed -> observing`` (if not already
    there) and, only when the classification is ``recovered``, further to
    ``observing -> recovered``. A ``not_recovered`` or ``unresolved``
    classification leaves the case in ``observing`` -- Phase 7 does not
    invent a rule that moves a case to ``FAILED`` merely because one
    observation attempt found a later failure; the state machine's
    ``-> FAILED`` edges remain available to a human/future phase, not
    auto-driven here (no architecture requirement specifies that
    automatic transition, and inventing one would be exactly the kind of
    fabricated business rule this phase must not add).

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case, :class:`NoExecutedActionError` if the case has no
    executed action at all (defensive -- should not occur), and
    :class:`CaseNotObservableError` if the case is not in
    ``action_executed``/``observing``.
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    action = await _get_action_for_case(session, case_id)
    if action is None:
        raise NoExecutedActionError(case_id)
    if action.status != RecoveryActionStatus.EXECUTED.value:
        raise NoExecutedActionError(case_id)

    classification = await classify_outcome(session, case)

    latest = await get_outcome_for_case(session, case_id)
    if (
        latest is not None
        and latest.outcome == classification.outcome.value
        and latest.evidence_payment_id == classification.evidence_payment_id
    ):
        # Idempotent replay: identical evidence to what is already the
        # authoritative record. No new attempt, no state re-transition.
        # Checked BEFORE the case.state gate below deliberately: a case
        # that already reached the terminal `recovered` state (or is
        # sitting in `observing` with an unchanged `unresolved`/
        # `not_recovered` record) must still accept a repeat, identical
        # observation as a genuine no-op -- rejecting it with 409 merely
        # because the case moved on since the last call would defeat
        # idempotency for exactly the case (pun intended) that matters
        # most: re-observing after the case has already closed.
        return case, latest, False

    # Only a genuinely NEW observation (different evidence) requires the
    # case to actually be in an observable state -- a stale re-poll that
    # would have produced identical evidence never reaches here.
    if case.state not in _OBSERVABLE_FROM:
        raise CaseNotObservableError(case.state.value)

    # Captured now, before any operation below can call rollback() -- same
    # MissingGreenlet hazard, and the same fix, as
    # app.decision.actions.schedule_action/execute_action and
    # app.decision.service.decide_case.
    action_id = action.id
    case_id_value = case.id
    next_attempt_no = (latest.attempt_no + 1) if latest is not None else 1

    row = RecoveryOutcomeObservation(
        case_id=case_id_value,
        action_id=action_id,
        attempt_no=next_attempt_no,
        outcome=classification.outcome.value,
        is_terminal=classification.is_terminal,
        evidence_payment_id=classification.evidence_payment_id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have won the (action_id, attempt_no)
        # race. Re-check rather than assuming -- the database is what
        # actually enforced uniqueness (KI-008), same pattern as
        # app.decision.actions.
        current = await get_outcome_for_case(session, case_id)
        if current is not None:
            current_case = await recovery_service.get_case(session, case_id)
            return current_case, current, False
        raise

    try:
        updated_case = case
        if updated_case.state is RecoveryCaseState.ACTION_EXECUTED:
            updated_case = await recovery_service.transition_case(
                session,
                case_id,
                RecoveryCaseState.OBSERVING,
                actor=_OBSERVE_ACTOR,
                reason=f"outcome observed: {row.outcome}",
                enforce_preconditions=True,
            )
        if classification.outcome is ObservedOutcome.RECOVERED:
            updated_case = await recovery_service.transition_case(
                session,
                case_id,
                RecoveryCaseState.RECOVERED,
                actor=_OBSERVE_ACTOR,
                reason=f"outcome observed: {row.outcome}",
                enforce_preconditions=True,
            )
    except Exception:
        await session.rollback()
        raise

    return updated_case, row, True
