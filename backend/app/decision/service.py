"""Decision service (Phase 5C): orchestration, persistence, idempotency.

``decide_case`` is the single entry point. It never re-implements policy --
every admissibility rule lives in ``app.decision.policy.evaluate`` (Phase
5B), which this module treats as the sole authority. This module's job is
purely to gather deterministic context, call that authority once, and
persist the result safely under concurrency.

Scope boundary (Phase 5 Architecture Revision): this module decides. It
never calls an AI provider, never executes a recovery action, never marks
anything "recovered", and never schedules real-world work -- those are
Phase 6/7/8.

Discovered dependency (see the Phase 5C implementation report): the
persisted ``Diagnosis`` row does not retain ``evidence_sufficiency`` /
``signals_conflict`` -- Phase 4 computes them transiently in
``RecoveryContext`` and never stores them. Rather than inventing new logic
or persisting new columns on ``Diagnosis`` (which would be a Phase 4
schema change, out of this workstream's scope), this module recomputes
them by calling the existing, unmodified
``app.ai.context_builder.build_recovery_context`` -- the same pure,
database-only function Phase 4 already uses. This is not a second
diagnosis and not an AI call; it is the identical deterministic query
Phase 4 already runs, called again for its two typed signals.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.ai.context_builder import build_recovery_context
from app.ai.schema import DiagnosisDisposition, RecoveryStrategy
from app.core.errors import CaseNotDecidableError, NoDiagnosisToDecideError
from app.decision.policy import PolicyInput, evaluate
from app.decision.schema import DECISION_ENGINE_VERSION
from app.models.decision import DecisionResult as DecisionResultRow
from app.models.payment import Payment, PaymentStatus
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.recovery import service as recovery_service
from app.services.diagnosis import get_latest_diagnosis

_DECIDABLE_FROM = {RecoveryCaseState.DIAGNOSED}
_ACTOR = "system:decide"

logger = logging.getLogger("app.decision.service")

# This is the DECISION-level retry count (how many separate diagnose/decide
# cycles this case has already been through) -- distinct from the
# WITHIN-ACTION attempt count Phase 6's completion introduced
# (app.decision.actions.execute_action's bounded attempt_no loop, capped
# by the same RETRY_CAP). The state machine has no back-edge from
# ACTION_EXECUTED/OBSERVING to DIAGNOSING (app/recovery/state_machine.py),
# so a case can only ever be decided once -- there is no re-diagnosis loop
# to drive this count above 0. Left hardcoded deliberately, not silently:
# introducing a case-relaunch loop is a distinct piece of scope (a new
# state-machine edge) this Phase 6 completion did not add. Replace this
# once such a loop exists, never before.
_RETRY_COUNT_PENDING_PHASE_6 = 0


async def _has_later_successful_payment(
    session: AsyncSession, customer_id: UUID, after: datetime
) -> bool:
    """Same "already paid by another attempt" check the risk queue uses
    (app/risk/service.py::_at_risk_payments) -- a successful payment for
    the same customer occurring after the failed one being decided.
    """
    later_success = aliased(Payment)
    stmt = (
        select(later_success.id)
        .where(later_success.customer_id == customer_id)
        .where(later_success.status == PaymentStatus.SUCCEEDED)
        .where(later_success.occurred_at > after)
        .limit(1)
    )
    return (await session.scalar(stmt)) is not None


async def _get_existing_decision(
    session: AsyncSession, case_id: UUID, diagnosis_id: UUID
) -> DecisionResultRow | None:
    result: DecisionResultRow | None = await session.scalar(
        select(DecisionResultRow)
        .where(DecisionResultRow.case_id == case_id)
        .where(DecisionResultRow.diagnosis_id == diagnosis_id)
    )
    return result


async def get_decision_for_case(session: AsyncSession, case_id: UUID) -> DecisionResultRow | None:
    """The decision for a case's *current* diagnosis, if one has been made
    yet -- the read-only counterpart to ``decide_case`` (Phase 5F: exposed
    by ``GET /recovery/cases/{id}``). Returns ``None`` for a case with no
    diagnosis yet or one whose diagnosis has not been decided, never
    raises for either of those ordinary states.
    """
    diagnosis = await get_latest_diagnosis(session, case_id)
    if diagnosis is None:
        return None
    return await _get_existing_decision(session, case_id, diagnosis.id)


async def decide_case(
    session: AsyncSession, case_id: UUID
) -> tuple[RecoveryCase, DecisionResultRow]:
    """Decide a case currently in ``DIAGNOSED``.

    Idempotent on ``(case_id, diagnosis_id)`` (KI-008-safe: a database
    unique constraint, not a pre-check, is authoritative -- see
    ``DecisionResult.__table_args__``). Concurrent identical requests
    resolve to exactly one persisted row; the loser recovers by re-reading
    it, never by trusting an earlier SELECT.

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case, :class:`CaseNotDecidableError` if the case has no
    diagnosis to decide against and is not currently in ``DIAGNOSED``, and
    :class:`NoDiagnosisToDecideError` if, defensively, no diagnosis exists
    at all.

    Idempotency-check ordering matters here: a case that has *already*
    been decided has moved on to ``DECISION_PENDING`` (the state machine
    has no back-edge -- Phase 3's linear design), so a repeated sequential
    call for the same ``(case_id, diagnosis_id)`` would incorrectly hit
    "not decidable" if the state were checked first. Instead this looks
    for an existing decision against the case's current diagnosis *before*
    enforcing the state gate, so both a concurrent race and a later
    sequential replay resolve to the same idempotent result. This initial
    lookup is an optimization only -- the database unique constraint plus
    the flush/recheck below remains the actual authority for concurrent
    races (KI-008).
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    diagnosis = await get_latest_diagnosis(session, case_id)

    if diagnosis is not None:
        existing = await _get_existing_decision(session, case_id, diagnosis.id)
        if existing is not None:
            return case, existing

    if case.state not in _DECIDABLE_FROM:
        raise CaseNotDecidableError(case.state.value)
    if diagnosis is None:
        raise NoDiagnosisToDecideError(case_id)

    # Captured now, before any operation below can call rollback() (see
    # the loser branch further down). session.rollback() expires every
    # attribute of every ORM object attached to the session, regardless
    # of expire_on_commit; a later plain `diagnosis.id` read would then
    # be an expired-attribute access -- implicit lazy-load IO attempted
    # from synchronous attribute-access code, which asyncio SQLAlchemy
    # forbids (raises MissingGreenlet, confirmed by dedicated
    # investigation: 20/20 reproductions with the plain attribute read,
    # 0/20 once this captured local id is used instead). Everything
    # below this point must use diagnosis_id, never diagnosis.id again.
    diagnosis_id = diagnosis.id

    # evidence_sufficiency / signals_conflict are not persisted on
    # Diagnosis (see module docstring) -- recomputed via the same,
    # unmodified Phase 4 function, not reinvented here.
    context = await build_recovery_context(session, case)
    already_paid = await _has_later_successful_payment(
        session, case.customer_id, context.payment.occurred_at
    )

    policy_input = PolicyInput(
        disposition=DiagnosisDisposition(diagnosis.disposition),
        candidate_strategy=RecoveryStrategy(diagnosis.recommended_strategy),
        recommended_delay_hours=diagnosis.recommended_delay_hours,
        evidence_sufficiency=context.evidence_sufficiency,
        signals_conflict=context.signals_conflict,
        retry_count=_RETRY_COUNT_PENDING_PHASE_6,
        already_paid=already_paid,
        now=datetime.now(UTC),
    )
    outcome = evaluate(policy_input)

    row = DecisionResultRow(
        case_id=case.id,
        diagnosis_id=diagnosis_id,
        recoverability=outcome.recoverability.value,
        candidate_strategy=outcome.candidate_strategy.value,
        approved_strategy=outcome.approved_strategy.value,
        decision_status=outcome.decision_status.value,
        rationale=[entry.model_dump() for entry in outcome.rationale],
        scheduled_not_before=outcome.scheduled_not_before,
        decision_engine_version=DECISION_ENGINE_VERSION,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have won the (case_id, diagnosis_id)
        # race. Re-check rather than assuming -- the database, not this
        # branch, is what actually enforced uniqueness (KI-008). Same
        # shape as app/recovery/service.py::open_case's existing, proven
        # recovery pattern: rollback, then continue on the same session.
        #
        # Uses diagnosis_id (captured before this rollback), never
        # diagnosis.id here: rollback() expires every attribute on every
        # ORM object attached to this session, and a plain `diagnosis.id`
        # read at this point would be an expired-attribute access --
        # implicit lazy-load IO from synchronous attribute-access code,
        # which raises sqlalchemy.exc.MissingGreenlet under asyncio
        # (confirmed root cause; see the Phase 5C ORM lifecycle
        # investigation report -- 20/20 reproductions with the plain
        # attribute read, 0/20 once the captured local id is used).
        existing = await _get_existing_decision(session, case_id, diagnosis_id)
        if existing is not None:
            current_case = await recovery_service.get_case(session, case_id)
            return current_case, existing
        raise

    # The state transition commits in the same session/transaction as the
    # already-flushed (not yet committed) decision row above, so both
    # become durable together or not at all -- no distributed transaction,
    # just the existing shared-session convention already used throughout
    # this codebase (see app/services/diagnosis.py::diagnose_case).
    try:
        updated_case = await recovery_service.transition_case(
            session,
            case_id,
            RecoveryCaseState.DECISION_PENDING,
            actor=_ACTOR,
            reason=f"decision: {outcome.decision_status.value} ({outcome.approved_strategy.value})",
            # Phase 5D: this is the path Phase 5 drives, so precondition
            # enforcement is turned on here (see
            # app/recovery/preconditions.py's DIAGNOSED -> DECISION_PENDING
            # checker) -- the just-flushed `row` above is what it verifies.
            enforce_preconditions=True,
        )
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "case decided",
        extra={
            "case_id": str(updated_case.id),
            "decision_id": str(row.id),
            "diagnosis_id": str(diagnosis_id),
            "decision_status": outcome.decision_status.value,
            "approved_strategy": outcome.approved_strategy.value,
            "recoverability": outcome.recoverability.value,
        },
    )
    return updated_case, row
