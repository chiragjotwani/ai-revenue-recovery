"""Recovery case lifecycle operations.

This module is the only place that creates recovery cases or changes their
state. Every state change is validated against
``app/recovery/state_machine.py`` and recorded as an immutable
``RecoveryCaseTransition`` row in the same database transaction as the
update to ``RecoveryCase.state`` (Section 16: use a state-transition
service; illegal transitions must raise).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    PaymentNotFoundError,
    PaymentNotRecoverableError,
    RecoveryCaseNotFoundError,
    TransitionPreconditionError,
)
from app.models.payment import Payment, PaymentStatus
from app.models.recovery import RecoveryCase, RecoveryCaseState, RecoveryCaseTransition
from app.recovery import preconditions
from app.recovery.state_machine import INITIAL_STATE, assert_transition_allowed, is_terminal


async def _get_case_by_payment(session: AsyncSession, payment_id: UUID) -> RecoveryCase | None:
    case: RecoveryCase | None = await session.scalar(
        select(RecoveryCase).where(RecoveryCase.payment_id == payment_id)
    )
    return case


async def get_case(session: AsyncSession, case_id: UUID) -> RecoveryCase:
    case = await session.get(RecoveryCase, case_id)
    if case is None:
        raise RecoveryCaseNotFoundError(case_id)
    return case


async def get_case_transitions(
    session: AsyncSession, case_id: UUID
) -> Sequence[RecoveryCaseTransition]:
    """Ordered (oldest first) transition history for a case."""
    result = await session.scalars(
        select(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .order_by(RecoveryCaseTransition.created_at, RecoveryCaseTransition.id)
    )
    return result.all()


async def list_cases(
    session: AsyncSession, state: RecoveryCaseState | None = None
) -> Sequence[RecoveryCase]:
    stmt = select(RecoveryCase).order_by(RecoveryCase.opened_at.desc(), RecoveryCase.id.desc())
    if state is not None:
        stmt = stmt.where(RecoveryCase.state == state)
    return (await session.scalars(stmt)).all()


async def open_case(
    session: AsyncSession, payment_id: UUID, *, actor: str = "system:open"
) -> tuple[RecoveryCase, bool]:
    """Open a recovery case for a failed payment, in the ``DETECTED`` state.

    Returns ``(case, created)``. Idempotent: if a case already exists for
    this payment (there can only ever be one -- ``payment_id`` is unique),
    that existing case is returned with ``created=False`` rather than a
    second being created. A concurrent opener that wins the race is handled
    the same way.

    Raises :class:`PaymentNotFoundError` if the payment id is unknown and
    :class:`PaymentNotRecoverableError` if the payment is not ``failed``.
    """
    existing = await _get_case_by_payment(session, payment_id)
    if existing is not None:
        return existing, False

    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise PaymentNotFoundError(payment_id)
    if payment.status is not PaymentStatus.FAILED:
        raise PaymentNotRecoverableError(payment_id, payment.status.value)

    case = RecoveryCase(
        payment_id=payment.id,
        customer_id=payment.customer_id,
        state=INITIAL_STATE,
    )
    session.add(case)
    try:
        await session.flush()
        session.add(
            RecoveryCaseTransition(
                case_id=case.id,
                from_state=None,
                to_state=INITIAL_STATE,
                reason="case opened",
                actor=actor,
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # A concurrent request opened the case first. Return theirs.
        raced = await _get_case_by_payment(session, payment_id)
        if raced is not None:
            return raced, False
        raise

    await session.refresh(case)
    return case, True


async def transition_case(
    session: AsyncSession,
    case_id: UUID,
    to_state: RecoveryCaseState,
    *,
    actor: str,
    reason: str | None = None,
    enforce_preconditions: bool = False,
) -> RecoveryCase:
    """Move a case to ``to_state``, recording the change.

    Raises :class:`RecoveryCaseNotFoundError` for an unknown case and
    :class:`IllegalStateTransitionError` if the state machine does not
    permit ``current_state -> to_state``. On the illegal path nothing is
    written.

    ``enforce_preconditions`` (default ``False`` -- the Phase 3 shape-only
    contract) additionally checks that the artifact a forward transition
    depends on actually exists (see ``app/recovery/preconditions.py``) and
    raises :class:`TransitionPreconditionError` if not. Phase 5+ turns this
    on for the paths it drives.
    """
    case = await get_case(session, case_id)
    from_state = case.state

    assert_transition_allowed(from_state, to_state)

    if enforce_preconditions:
        unmet = await preconditions.check(session, case, to_state)
        if unmet is not None:
            raise TransitionPreconditionError(from_state.value, to_state.value, unmet)

    session.add(
        RecoveryCaseTransition(
            case_id=case.id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            actor=actor,
        )
    )
    case.state = to_state
    if is_terminal(to_state):
        case.closed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(case)
    return case
