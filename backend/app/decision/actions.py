"""Action scheduling & execution (Phase 6: Action Executor).

Scope boundary, mirroring ``app.decision.service``'s own docstring: this
module never calls an AI provider, never invents a strategy the policy
engine did not already approve, and never fabricates an external side
effect that did not happen. It executes ONLY an already policy-approved
``DecisionResult`` (``app.decision.schema.DecisionStatus.APPROVED``), after
validating recovery-state preconditions -- the boundary ADR-003 requires:

    Diagnosis -> Policy Engine -> Recovery State Validation
    -> Idempotency Validation -> Action Executor

Two entry points, matching the two forward transitions this phase drives:

* :func:`schedule_action` -- ``decision_pending -> action_scheduled``.
  Creates a ``RecoveryAction`` row identified by
  ``(case_id, action_type, decision_result_id)`` (the action-identity
  contract fixed in ``docs/recovery/action-idempotency.md`` before this
  phase existed). ``action_type`` is always the decision's own
  ``approved_strategy`` -- this module never accepts a strategy as a
  parameter, so a caller (including any future AI-facing surface) cannot
  choose what gets scheduled; only a persisted, policy-approved decision
  can.
* :func:`execute_action` -- ``action_scheduled -> action_executed``.
  Creates a ``RecoveryActionExecution`` row (attempt 1) with a derived
  idempotency key, before any external effect is attempted. No payment-
  provider or customer-messaging integration exists in this repository
  (Phase 6 does not invent one -- see ``app.models.action`` for the
  ``ActionExecutionOutcome`` split this implies).

Idempotency (KI-008 lesson, applied identically to both entry points): the
existence check that runs first is an optimization only. The actual
authority is the database unique constraint plus the flush/``IntegrityError``
recheck below -- never a bare "SELECT then INSERT". A concurrent duplicate
request resolves to exactly one persisted row; the loser recovers by
re-reading it, never by trusting an earlier SELECT.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    CaseNotExecutableError,
    CaseNotSchedulableError,
    DecisionNotApprovedError,
    NoApprovedDecisionError,
    NoScheduledActionError,
)
from app.decision.schema import DecisionStatus
from app.decision.service import get_decision_for_case
from app.models.action import ActionExecutionOutcome, RecoveryAction, RecoveryActionExecution
from app.models.action import RecoveryActionStatus as _Status
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.recovery import service as recovery_service

_SCHEDULABLE_FROM = {RecoveryCaseState.DECISION_PENDING}
_EXECUTABLE_FROM = {RecoveryCaseState.ACTION_SCHEDULED}

_SCHEDULE_ACTOR = "system:schedule_action"
_EXECUTE_ACTOR = "system:execute_action"

logger = logging.getLogger("app.decision.actions")

# Approved strategies that carry no external side effect at all -- their
# "execution" is the completion itself (already-paid, or a handoff to a
# human queue). Every other approved strategy (retry,
# request_payment_method_update, contact_customer) would require a real
# external integration this repository does not implement -- see
# app.models.action.ActionExecutionOutcome.
_NO_SIDE_EFFECT_ACTION_TYPES = frozenset({"no_action", "manual_review"})


async def _get_existing_action(
    session: AsyncSession, case_id: UUID, action_type: str, decision_result_id: UUID
) -> RecoveryAction | None:
    # executions is eager-loaded (selectinload, not lazy) on every path that
    # returns a RecoveryAction: ActionOut includes it, and a plain lazy
    # relationship access on an object read back via a fresh SELECT would be
    # an implicit-IO attribute access -- the same MissingGreenlet hazard
    # documented for app.decision.service (a bare `diagnosis.id` read after
    # rollback), just triggered by a relationship instead of a scalar.
    result: RecoveryAction | None = await session.scalar(
        select(RecoveryAction)
        .options(selectinload(RecoveryAction.executions))
        .where(RecoveryAction.case_id == case_id)
        .where(RecoveryAction.action_type == action_type)
        .where(RecoveryAction.decision_result_id == decision_result_id)
    )
    return result


async def get_action_for_case(session: AsyncSession, case_id: UUID) -> RecoveryAction | None:
    """The action for a case's *current* decision, if one has been
    scheduled yet -- the read-only counterpart to :func:`schedule_action`
    (exposed by ``GET /recovery/cases/{id}``). Returns ``None`` for a case
    with no decision yet, or a decision that no action has been scheduled
    for, never raises for either of those ordinary states.
    """
    decision = await get_decision_for_case(session, case_id)
    if decision is None:
        return None
    result: RecoveryAction | None = await session.scalar(
        select(RecoveryAction)
        .options(selectinload(RecoveryAction.executions))
        .where(RecoveryAction.case_id == case_id)
        .where(RecoveryAction.action_type == decision.approved_strategy)
        .where(RecoveryAction.decision_result_id == decision.id)
    )
    return result


async def schedule_action(
    session: AsyncSession, case_id: UUID
) -> tuple[RecoveryCase, RecoveryAction, bool]:
    """Schedule the action for a case's current, policy-approved decision.

    Returns ``(case, action, created)``. Idempotent on
    ``(case_id, action_type, decision_result_id)``: a repeat call returns
    the existing row with ``created=False``.

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case, :class:`NoApprovedDecisionError` if the case has no
    decision at all (defensive -- should not occur, see the docstring),
    :class:`DecisionNotApprovedError` if the decision is not
    ``approved`` (an escalated or rejected decision must never reach
    scheduling), and :class:`CaseNotSchedulableError` if the case is not
    in ``decision_pending`` and has no existing action to replay.
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    decision = await get_decision_for_case(session, case_id)
    if decision is None:
        raise NoApprovedDecisionError(case_id)

    existing = await _get_existing_action(session, case.id, decision.approved_strategy, decision.id)
    if existing is not None:
        return case, existing, False

    if decision.decision_status != DecisionStatus.APPROVED.value:
        raise DecisionNotApprovedError(decision.decision_status)
    if case.state not in _SCHEDULABLE_FROM:
        raise CaseNotSchedulableError(case.state.value)

    # Captured now, before any operation below can call rollback() (see the
    # loser branch further down). session.rollback() expires every
    # attribute of every ORM object attached to the session regardless of
    # expire_on_commit; a later plain `decision.id` / `decision.approved_strategy`
    # / `case.id` read would then be an expired-attribute access -- implicit
    # lazy-load IO from synchronous attribute-access code, which asyncio
    # SQLAlchemy forbids (raises MissingGreenlet -- the exact defect
    # app.decision.service.decide_case's own docstring documents and this
    # module must not repeat). Everything below this point uses these
    # captured locals, never the ORM attributes again.
    case_id_value = case.id
    approved_strategy = decision.approved_strategy
    decision_id = decision.id

    row = RecoveryAction(
        case_id=case_id_value,
        decision_result_id=decision_id,
        action_type=approved_strategy,
        status=_Status.SCHEDULED.value,
    )
    # A freshly scheduled action always has zero executions -- set this
    # explicitly rather than leaving the relationship unloaded. Assignment
    # (unlike a read) never issues a query, so this is safe, and it means
    # ActionOut.model_validate(row) below never touches an unloaded
    # relationship on this object.
    row.executions = []
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have won the action-identity race.
        # Re-check rather than assuming -- the database is what actually
        # enforced uniqueness (KI-008), same pattern as
        # app.decision.service.decide_case. Uses the captured locals above,
        # never case.id / decision.id / decision.approved_strategy again.
        existing = await _get_existing_action(
            session, case_id_value, approved_strategy, decision_id
        )
        if existing is not None:
            current_case = await recovery_service.get_case(session, case_id)
            return current_case, existing, False
        raise

    try:
        updated_case = await recovery_service.transition_case(
            session,
            case_id,
            RecoveryCaseState.ACTION_SCHEDULED,
            actor=_SCHEDULE_ACTOR,
            reason=f"action scheduled: {row.action_type}",
            enforce_preconditions=True,
        )
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "action scheduled",
        extra={
            "case_id": str(updated_case.id),
            "action_id": str(row.id),
            "decision_id": str(decision_id),
            "action_type": approved_strategy,
        },
    )
    return updated_case, row, True


async def execute_action(
    session: AsyncSession, case_id: UUID
) -> tuple[RecoveryCase, RecoveryAction, RecoveryActionExecution, bool]:
    """Execute the scheduled action for a case's current decision.

    Returns ``(case, action, execution, created)``. Idempotent on
    ``(action_id, attempt_no=1)``: a repeat call returns the existing
    execution with ``created=False`` and never creates a second attempt or
    a second external effect.

    ``NO_ACTION`` and ``MANUAL_REVIEW`` are always safe, first-class
    completions with :data:`~app.models.action.ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED`
    -- never treated as an error, never triggering any external call.
    Every other approved strategy records
    :data:`~app.models.action.ActionExecutionOutcome.DEFERRED_NO_INTEGRATION`
    (see the module docstring): this repository has no payment-provider or
    customer-messaging client to invoke, and Phase 6 does not invent one.

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case, :class:`NoScheduledActionError` if no action has been
    scheduled for this case's current decision (defensive), and
    :class:`CaseNotExecutableError` if the case is not in
    ``action_scheduled`` and has no existing execution to replay.
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    action = await get_action_for_case(session, case_id)
    if action is None:
        raise NoScheduledActionError(case_id)

    existing = await session.scalar(
        select(RecoveryActionExecution)
        .where(RecoveryActionExecution.action_id == action.id)
        .where(RecoveryActionExecution.attempt_no == 1)
    )
    if existing is not None:
        # `action` was loaded (with `executions` eager-loaded at that
        # instant) before this `existing` query ran. If a concurrent
        # request committed its execution in between, `action.executions`
        # is a stale, empty snapshot even though `existing` just found a
        # real row. A second call to get_action_for_case is NOT enough to
        # fix this: `action` is already in this session's identity map, and
        # SQLAlchemy does not re-run an eager load for an
        # already-populated relationship on an identity-mapped,
        # unexpired object -- a plain re-SELECT silently returns the same
        # stale Python object. An explicit refresh is required. Caught by
        # test_concurrent_execute_requests_produce_exactly_one_execution
        # (20-way concurrency), not by a sequential run.
        await session.refresh(action, attribute_names=["executions"])
        return case, action, existing, False

    if case.state not in _EXECUTABLE_FROM:
        raise CaseNotExecutableError(case.state.value)

    # Captured now, before any operation below can call rollback() -- same
    # MissingGreenlet hazard, and the same fix, as schedule_action above
    # and app.decision.service.decide_case: rollback() expires every
    # attribute on every ORM object attached to this session, so a later
    # plain `action.id` / `action.action_type` read would be an expired-
    # attribute access. Everything below uses these captured locals, never
    # the ORM attributes again.
    action_id = action.id
    action_type = action.action_type
    case_id_value = case.id

    outcome = (
        ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED
        if action_type in _NO_SIDE_EFFECT_ACTION_TYPES
        else ActionExecutionOutcome.DEFERRED_NO_INTEGRATION
    )
    execution = RecoveryActionExecution(
        action_id=action_id,
        attempt_no=1,
        idempotency_key=f"arr:{case_id_value}:{action_type}:1",
        outcome=outcome.value,
    )
    # Append to the already-loaded relationship (action.executions was
    # eager-loaded by get_action_for_case above) rather than a bare
    # session.add: this keeps action.executions accurate in memory for the
    # ActionOut this function returns -- expire_on_commit=False means the
    # collection would otherwise stay stale (missing this new row) after
    # the commit below. The append also schedules the insert via the
    # relationship's default save-update cascade.
    action.executions.append(execution)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Same KI-008-safe recheck pattern as schedule_action /
        # decide_case: the database's unique constraint on
        # (action_id, attempt_no) is authoritative, not this branch. Uses
        # the captured action_id local, never action.id again.
        existing = await session.scalar(
            select(RecoveryActionExecution)
            .where(RecoveryActionExecution.action_id == action_id)
            .where(RecoveryActionExecution.attempt_no == 1)
        )
        if existing is not None:
            current_case = await recovery_service.get_case(session, case_id)
            current_action = await get_action_for_case(session, case_id)
            if current_action is None:
                raise NoScheduledActionError(case_id) from None
            return current_case, current_action, existing, False
        raise

    action.status = _Status.EXECUTED.value
    try:
        updated_case = await recovery_service.transition_case(
            session,
            case_id,
            RecoveryCaseState.ACTION_EXECUTED,
            actor=_EXECUTE_ACTOR,
            reason=f"action executed: {action.action_type} ({outcome.value})",
            enforce_preconditions=True,
        )
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "action executed",
        extra={
            "case_id": str(updated_case.id),
            "action_id": str(action_id),
            "execution_id": str(execution.id),
            "action_type": action_type,
            "execution_outcome": outcome.value,
        },
    )
    return updated_case, action, execution, True


__all__ = [
    "execute_action",
    "get_action_for_case",
    "schedule_action",
]
