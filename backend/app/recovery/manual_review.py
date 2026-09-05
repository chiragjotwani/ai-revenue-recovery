"""Human-in-the-loop manual review resolution (Phase 17: Advanced
Autonomous Recovery, human-in-the-loop scope).

Closes a real gap this project's own audit found: a case whose approved
decision strategy is ``manual_review`` previously auto-completed through
``ACTION_EXECUTED`` with ``ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED``
exactly like ``no_action`` -- no operator ever actually reviewed
anything, despite the policy engine's own escalation rules (fraud
suspicion, insufficient evidence, conflicting signals -- see
``app.decision.policy``) existing specifically to route a case to a
human. ``app.decision.actions.execute_action`` now instead transitions
such a case to ``RecoveryCaseState.PENDING_MANUAL_REVIEW`` and stops;
this module is how a human closes the loop.

Deliberately narrow scope (owner-confirmed before implementation): an
operator may resolve to ``ABANDONED`` or ``FAILED`` only -- never a full
re-decision loop back into ``decision_pending`` (would reopen
decision/action identity questions this phase does not scope) and never
``RECOVERED`` (no authoritative payment evidence exists merely because a
human looked at the case; only Phase 7's evidence-based
``observe_outcome`` may ever set that state, and this module never calls
it). ADR-003 is unaffected: this is a human decision recorded through an
API a human calls, not the AI choosing or executing anything.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CaseNotPendingManualReviewError, ManualReviewAlreadyResolvedError
from app.models.manual_review import ManualReviewOutcome, ManualReviewResolution
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.recovery import service as recovery_service

_RESOLVABLE_FROM = {RecoveryCaseState.PENDING_MANUAL_REVIEW}
_RESOLVE_ACTOR = "operator:resolve_manual_review"

_TARGET_STATE_BY_RESOLUTION = {
    ManualReviewOutcome.ABANDONED: RecoveryCaseState.ABANDONED,
    ManualReviewOutcome.FAILED: RecoveryCaseState.FAILED,
}


async def get_manual_review_resolution(
    session: AsyncSession, case_id: UUID
) -> ManualReviewResolution | None:
    """The resolution for a case, if one has been recorded -- the
    read-only counterpart to :func:`resolve_manual_review` (exposed by
    ``GET /recovery/cases/{id}``). Returns ``None`` for a case that has
    never been escalated to manual review, or one still awaiting
    resolution.
    """
    result: ManualReviewResolution | None = await session.scalar(
        select(ManualReviewResolution).where(ManualReviewResolution.case_id == case_id)
    )
    return result


async def resolve_manual_review(
    session: AsyncSession,
    case_id: UUID,
    *,
    resolution: ManualReviewOutcome,
    note: str,
    actor: str = _RESOLVE_ACTOR,
) -> tuple[RecoveryCase, ManualReviewResolution]:
    """An operator resolves a case's manual-review escalation.

    Returns ``(case, resolution_row)``. NOT idempotent on repeat calls
    with the same arguments -- unlike every other write in this system's
    recovery pipeline, a case can only leave ``pending_manual_review``
    once (the state machine has no edge back into it, mirroring a real
    "a human already made this call" invariant), so a second resolution
    attempt is a genuine conflict
    (:class:`~app.core.errors.ManualReviewAlreadyResolvedError`), never a
    replay to collapse.

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case, :class:`~app.core.errors.CaseNotPendingManualReviewError`
    if the case is not in ``pending_manual_review``, and
    :class:`~app.core.errors.ManualReviewAlreadyResolvedError` if it has
    already been resolved (including a concurrent resolution that won a
    race against this one).
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown

    existing = await get_manual_review_resolution(session, case_id)
    if existing is not None:
        raise ManualReviewAlreadyResolvedError(case_id)

    if case.state not in _RESOLVABLE_FROM:
        raise CaseNotPendingManualReviewError(case.state.value)

    row = ManualReviewResolution(
        case_id=case_id,
        resolution=resolution.value,
        note=note,
        actor=actor,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have won the resolution race -- the
        # unique constraint on case_id is authoritative (KI-008
        # discipline), not this branch. Re-check rather than assuming.
        raise ManualReviewAlreadyResolvedError(case_id) from None

    target_state = _TARGET_STATE_BY_RESOLUTION[resolution]
    try:
        updated_case = await recovery_service.transition_case(
            session,
            case_id,
            target_state,
            actor=actor,
            reason=f"manual review resolved: {resolution.value} ({note})",
            enforce_preconditions=False,
        )
    except Exception:
        await session.rollback()
        raise

    return updated_case, row


__all__ = ["get_manual_review_resolution", "resolve_manual_review"]
