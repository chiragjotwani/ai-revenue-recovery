"""Artifact preconditions for recovery-case transitions (Phase 4.1, WS-C).

Phase 3 built the state machine as a *workflow spine*: it validates the
SHAPE of a transition (is ``from -> to`` a legal edge?) but not whether the
work that edge represents has actually happened. That is fine for a spine
and was the documented Phase 3 contract -- but it must not be the eventual
production behaviour, because Phases 5-7 will compute real outcomes
(recovered revenue, control-group lift) from case state.

This module declares the *precondition contract*: for each forward
transition, the artifact that must exist before that transition is
legitimate. It is:

* **enforced now** only where the backing artifact model already exists
  (today: ``diagnosing -> diagnosed`` requires a persisted ``Diagnosis``),
  and only when a caller opts in via ``transition_case(...,
  enforce_preconditions=True)``. The default stays ``False`` so every
  existing Phase 3 path and test is unchanged.
* **declared but not yet checkable** for later transitions whose artifact
  (a policy ``DecisionResult``, an action/execution record, an observed
  success event) is introduced by a future phase. ``check`` treats those
  as "no check available yet" rather than silently passing forever.

Phase 5 is expected to flip ``enforce_preconditions`` on for the paths it
drives and to fill in the ``checker`` for ``decision_pending ->
action_scheduled`` once ``DecisionResult`` exists.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.schema import DecisionStatus
from app.models.action import RecoveryAction
from app.models.decision import DecisionResult
from app.models.diagnosis import Diagnosis
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.services.diagnosis import get_latest_diagnosis

_S = RecoveryCaseState

# A checker returns None if the precondition is met, or a human-readable
# reason string if it is not.
Checker = Callable[[AsyncSession, RecoveryCase], Awaitable[str | None]]


async def _requires_persisted_diagnosis(session: AsyncSession, case: RecoveryCase) -> str | None:
    count = await session.scalar(
        select(func.count()).select_from(Diagnosis).where(Diagnosis.case_id == case.id)
    )
    if not count:
        return "no persisted diagnosis exists for this case"
    return None


async def _requires_decision_result(session: AsyncSession, case: RecoveryCase) -> str | None:
    """Phase 5: the diagnosis must actually have been handed to, and
    resolved by, the decision engine -- i.e. a ``DecisionResult`` exists
    for the case's current diagnosis. Checked from inside
    ``decide_case``'s own transaction (Phase 5D), after the
    ``DecisionResult`` row has been flushed but not yet committed: the
    flushed-but-uncommitted row is visible to this query because it runs
    on the same session/transaction (ordinary Postgres read-your-writes
    behaviour), not because of any cross-transaction visibility.
    """
    diagnosis = await get_latest_diagnosis(session, case.id)
    if diagnosis is None:
        return "no persisted diagnosis exists for this case"
    count = await session.scalar(
        select(func.count())
        .select_from(DecisionResult)
        .where(DecisionResult.case_id == case.id)
        .where(DecisionResult.diagnosis_id == diagnosis.id)
    )
    if not count:
        return "no DecisionResult exists for this case's current diagnosis"
    return None


async def _requires_approved_decision(session: AsyncSession, case: RecoveryCase) -> str | None:
    """Phase 5: scheduling an action requires a policy-*approved*
    DecisionResult for the case's current diagnosis -- an escalated or
    rejected decision must never reach ``action_scheduled`` (that would
    bypass the policy engine's own verdict). This does not invent a new
    business rule: it only checks the ``decision_status`` Phase 5B/5C
    already computed and persisted.
    """
    diagnosis = await get_latest_diagnosis(session, case.id)
    if diagnosis is None:
        return "no persisted diagnosis exists for this case"
    decision = await session.scalar(
        select(DecisionResult)
        .where(DecisionResult.case_id == case.id)
        .where(DecisionResult.diagnosis_id == diagnosis.id)
    )
    if decision is None:
        return "no DecisionResult exists for this case's current diagnosis"
    if decision.decision_status != DecisionStatus.APPROVED.value:
        return (
            f"the decision for this case is '{decision.decision_status}', "
            "not 'approved' -- an action may only be scheduled for an "
            "approved decision"
        )
    return None


async def _requires_scheduled_action(session: AsyncSession, case: RecoveryCase) -> str | None:
    """Phase 6: executing an action requires that an action has actually
    been scheduled (``app.decision.actions.schedule_action``) for this
    case's current, policy-approved decision. Checked from inside
    ``execute_action``'s own transaction, after the ``RecoveryAction`` row
    was flushed in a prior transaction (ordinary Postgres read-your-writes
    for this session) -- mirrors ``_requires_decision_result`` exactly.
    """
    diagnosis = await get_latest_diagnosis(session, case.id)
    if diagnosis is None:
        return "no persisted diagnosis exists for this case"
    decision = await session.scalar(
        select(DecisionResult)
        .where(DecisionResult.case_id == case.id)
        .where(DecisionResult.diagnosis_id == diagnosis.id)
    )
    if decision is None:
        return "no DecisionResult exists for this case's current diagnosis"
    count = await session.scalar(
        select(func.count())
        .select_from(RecoveryAction)
        .where(RecoveryAction.case_id == case.id)
        .where(RecoveryAction.action_type == decision.approved_strategy)
        .where(RecoveryAction.decision_result_id == decision.id)
    )
    if not count:
        return "no RecoveryAction exists for this case's approved decision"
    return None


@dataclass(frozen=True)
class Precondition:
    """The artifact a forward transition depends on."""

    #: What must exist for ``from_state -> to_state`` to be legitimate.
    artifact: str
    #: Which phase introduces the artifact / its enforcement.
    provided_by_phase: str
    #: Runnable check, or None if the artifact model does not exist yet.
    checker: Checker | None


#: Forward-progress transitions and the artifact each one depends on.
#: Keyed by (from_state, to_state). Terminal/abandon edges are intentionally
#: absent -- moving a case to ABANDONED/FAILED never needs an artifact.
TRANSITION_PRECONDITIONS: dict[tuple[RecoveryCaseState, RecoveryCaseState], Precondition] = {
    (_S.DETECTED, _S.DIAGNOSING): Precondition(
        artifact="an open recovery case for a failed payment",
        provided_by_phase="Phase 3 (already guaranteed by open_case)",
        checker=None,  # open_case is the only way in; nothing extra to check
    ),
    (_S.DIAGNOSING, _S.DIAGNOSED): Precondition(
        artifact="a persisted, schema-validated Diagnosis row",
        provided_by_phase="Phase 4",
        checker=_requires_persisted_diagnosis,
    ),
    (_S.DIAGNOSED, _S.DECISION_PENDING): Precondition(
        artifact="the diagnosis has been handed to the decision engine",
        provided_by_phase="Phase 5D",
        checker=_requires_decision_result,
    ),
    (_S.DECISION_PENDING, _S.ACTION_SCHEDULED): Precondition(
        artifact="a policy-approved DecisionResult",
        provided_by_phase="Phase 5D",
        checker=_requires_approved_decision,
    ),
    (_S.ACTION_SCHEDULED, _S.ACTION_EXECUTED): Precondition(
        artifact="an action/execution record for the scheduled action",
        provided_by_phase="Phase 6",
        checker=_requires_scheduled_action,
    ),
    (_S.ACTION_EXECUTED, _S.OBSERVING): Precondition(
        artifact="the executed action has been recorded and is awaiting outcome",
        provided_by_phase="Phase 6",
        checker=None,
    ),
    (_S.OBSERVING, _S.RECOVERED): Precondition(
        artifact="an observed successful payment event linked to this recovery case",
        provided_by_phase="Phase 7",
        checker=None,
    ),
}


async def check(
    session: AsyncSession,
    case: RecoveryCase,
    to_state: RecoveryCaseState,
) -> str | None:
    """Return None if the precondition for ``case.state -> to_state`` is met
    (or cannot be checked yet), else a reason string.

    A transition with no declared precondition (e.g. ``-> ABANDONED``) is
    always allowed. A declared precondition whose ``checker`` is not yet
    implemented returns None here (documented, not silently forever-open --
    the entry exists so a future phase must fill it in).
    """
    pre = TRANSITION_PRECONDITIONS.get((case.state, to_state))
    if pre is None or pre.checker is None:
        return None
    return await pre.checker(session, case)
