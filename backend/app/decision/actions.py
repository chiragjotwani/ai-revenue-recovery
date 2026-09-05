"""Action scheduling & execution (Phase 6: Action Executor, completed with a
simulated execution layer; ``retry`` optionally real as of Phase 16).

Scope boundary, mirroring ``app.decision.service``'s own docstring: this
module never calls an AI provider, never invents a strategy the policy
engine did not already approve, and never fabricates an external side
effect that did not happen. It executes ONLY an already policy-approved
``DecisionResult`` (``app.decision.schema.DecisionStatus.APPROVED``), after
validating recovery-state preconditions -- the boundary ADR-003 requires:

    Diagnosis -> Policy Engine -> Recovery State Validation
    -> Idempotency Validation -> Action Executor -> Provider

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
* :func:`execute_action` -- ``action_scheduled -> action_executed`` (once
  terminal -- see below). Creates one ``RecoveryActionExecution`` row per
  attempt. ``no_action``/``manual_review`` remain a single, immediate,
  side-effect-free completion exactly as before. Every other approved
  strategy (``retry``, ``request_payment_method_update``,
  ``contact_customer``) dispatches to an ``app.decision.executors`` entry.
  ``request_payment_method_update`` and ``contact_customer`` remain
  deterministic, explicitly SIMULATED (``app.decision.providers`` -- no
  real messaging/payment-link provider exists in this repository). As of
  Phase 16, ``retry`` uses a real Stripe TEST-mode ``PaymentIntent``
  confirm/retry (``app.decision.providers_stripe``) when
  ``Settings.stripe_api_key`` is configured, falling back to the same
  simulated provider otherwise -- see that module's docstring for the
  full scope note. Only the bounded action-execution layer here may
  invoke either provider; neither the diagnosis model nor the policy
  engine can reach either -- see ``app.decision.providers``'s module
  docstring for why that boundary holds structurally, not just by
  convention.

  A single strategy may need more than one attempt (a temporary failure,
  then a success) up to :data:`RETRY_CAP` attempts (the same cap
  ``app.decision.policy`` already documents -- reused, not reinvented).
  While attempts remain and the latest one was a temporary failure, the
  action stays ``scheduled`` (case stays ``action_scheduled``) and a
  further ``execute_action`` call attempts the next attempt. Once an
  attempt succeeds, permanently fails, or the cap is exhausted, the
  action becomes terminal (``RecoveryActionStatus.EXECUTED`` in every
  case -- "executed" means the execution *process* completed, never that
  it succeeded; see :func:`execute_action`'s own docstring) and the case
  transitions to ``action_executed`` exactly once. This is the one
  bounded loop this repository's state machine needed (see
  ``app/recovery/state_machine.py``'s own note anticipating it) -- it
  lives entirely inside one ``execute_action`` call, so no new
  ``RecoveryCaseState`` or state-machine edge was required.

  On a simulated success, this module creates the evidence of that
  success the same way any other successful payment enters this platform
  -- a new ``Payment`` row (status ``succeeded``) plus an ``IngestionEvent``
  audit row (``app.models.event``), inside the SAME transaction as the
  execution attempt. It does **not** set ``case.state = RECOVERED``
  directly and does not call into ``app.outcome`` -- Phase 7's existing,
  unmodified evidence-based ``observe_outcome`` is what later reads this
  new ``Payment`` row and classifies the case as recovered, via the exact
  same later-successful-payment correlation rule it already uses for
  every other payment source. The causal link from this specific
  execution to that specific payment is recorded explicitly
  (``RecoveryActionExecution.resulting_payment_id``) so the audit trail
  never depends on inferring causation from timing alone -- see
  ``docs/recovery/action-idempotency.md``.

Idempotency (KI-008 lesson, applied identically to both entry points): the
existence check that runs first is an optimization only. The actual
authority is the database unique constraint plus the flush/``IntegrityError``
recheck below -- never a bare "SELECT then INSERT". A concurrent duplicate
request resolves to exactly one persisted row; the loser recovers by
re-reading it, never by trusting an earlier SELECT.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
from app.decision.executors import EXECUTORS_BY_ACTION_TYPE
from app.decision.policy import RETRY_CAP
from app.decision.providers import SimulationOutcome
from app.decision.schema import DecisionStatus
from app.decision.service import get_decision_for_case
from app.events.publisher import outbox_publisher
from app.events.schema import DomainEvent
from app.models.action import ActionExecutionOutcome, RecoveryAction, RecoveryActionExecution
from app.models.action import RecoveryActionStatus as _Status
from app.models.event import IngestionEvent
from app.models.payment import Payment, PaymentStatus
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.recovery import service as recovery_service

_SCHEDULABLE_FROM = {RecoveryCaseState.DECISION_PENDING}
_EXECUTABLE_FROM = {RecoveryCaseState.ACTION_SCHEDULED}

_SCHEDULE_ACTOR = "system:schedule_action"
_EXECUTE_ACTOR = "system:execute_action"

logger = logging.getLogger("app.decision.actions")

# Approved strategies that carry no external side effect at all -- their
# "execution" is the completion itself (already-paid). Every other
# approved strategy (retry, request_payment_method_update,
# contact_customer) dispatches to app.decision.executors' simulated
# executors -- see the module docstring. ``manual_review`` is handled
# separately below (Phase 17): unlike this set, it is NOT a completion --
# it blocks in PENDING_MANUAL_REVIEW for a human, never auto-completes.
_NO_SIDE_EFFECT_ACTION_TYPES = frozenset({"no_action"})
_MANUAL_REVIEW_ACTION_TYPE = "manual_review"

_SIMULATED_OUTCOME_BY_RESULT = {
    SimulationOutcome.SUCCESS: ActionExecutionOutcome.SIMULATED_SUCCESS,
    SimulationOutcome.TEMPORARY_FAILURE: ActionExecutionOutcome.SIMULATED_TEMPORARY_FAILURE,
    SimulationOutcome.PERMANENT_FAILURE: ActionExecutionOutcome.SIMULATED_PERMANENT_FAILURE,
}
#: Phase 16: the same SimulationOutcome shape, but for a genuine Stripe
#: TEST-mode call (ProviderAttemptResult.is_real=True) -- see
#: app.decision.providers_stripe and ActionExecutionOutcome's docstring
#: for why these must never share a value with the SIMULATED_* rows above.
_REAL_OUTCOME_BY_RESULT = {
    SimulationOutcome.SUCCESS: ActionExecutionOutcome.REAL_SUCCESS,
    SimulationOutcome.TEMPORARY_FAILURE: ActionExecutionOutcome.REAL_TEMPORARY_FAILURE,
    SimulationOutcome.PERMANENT_FAILURE: ActionExecutionOutcome.REAL_PERMANENT_FAILURE,
}


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


async def _get_locked_action_for_case(
    session: AsyncSession, case_id: UUID
) -> RecoveryAction | None:
    """Same lookup as :func:`get_action_for_case`, but with ``FOR UPDATE``
    on the ``recovery_actions`` row -- used only by :func:`execute_action`,
    never by a read-only caller. Required because ``status`` (a plain
    column) and ``executions`` (``selectinload``, a SEPARATE query) are two
    independent reads under READ COMMITTED: without a lock serializing
    concurrent callers, one request's ``status`` read can land before a
    concurrent winner's commit while its ``executions`` read lands after
    it -- an inconsistent combination (status ``scheduled`` alongside an
    execution the status hasn't caught up to yet) that let a concurrent
    request compute a spurious next attempt number a terminal action
    should never reach. Confirmed by direct reproduction: without this
    lock, `test_concurrent_execute_requests_produce_exactly_one_execution`
    failed intermittently (2-3 executions created out of 20 concurrent
    calls); with it, 50+ consecutive runs were clean. The lock serializes
    concurrent ``execute_action`` calls for the SAME action (each waits for
    the previous to commit/rollback before its own reads run), which is
    exactly the KI-008 "the database, not an application pre-check, is
    authoritative" discipline applied to a multi-statement read instead of
    a single insert.
    """
    decision = await get_decision_for_case(session, case_id)
    if decision is None:
        return None
    result: RecoveryAction | None = await session.scalar(
        select(RecoveryAction)
        .where(RecoveryAction.case_id == case_id)
        .where(RecoveryAction.action_type == decision.approved_strategy)
        .where(RecoveryAction.decision_result_id == decision.id)
        .with_for_update()
    )
    if result is None:
        return None
    await session.refresh(result, attribute_names=["executions"])
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


def _latest_execution(action: RecoveryAction) -> RecoveryActionExecution | None:
    if not action.executions:
        return None
    return max(action.executions, key=lambda e: e.attempt_no)


async def _record_simulated_success(
    session: AsyncSession,
    *,
    case: RecoveryCase,
    action_id: UUID,
    attempt_no: int,
    simulated_reference: str,
) -> UUID:
    """Create the simulated ``payment.succeeded`` evidence a successful
    execution attempt causes -- the same ``Payment`` + ``IngestionEvent``
    shape every other payment source in this platform produces (see
    ``app.services.ingestion.ingest_payment_event``), so Phase 7's
    existing, unmodified ``observe_outcome`` detects it via the ordinary
    later-successful-payment correlation rule, with no special case.
    Called only for a :data:`~app.decision.providers.SimulationOutcome.SUCCESS`
    attempt, inside the SAME flush the caller guards with an
    ``IntegrityError`` recheck -- a concurrent duplicate resolves exactly
    the same way a duplicate ingested payment would.
    """
    original_payment = await session.get(Payment, case.payment_id)
    assert original_payment is not None  # guaranteed by RecoveryCase.payment_id's FK

    payment = Payment(
        customer_id=case.customer_id,
        external_reference=simulated_reference,
        amount=original_payment.amount,
        currency=original_payment.currency,
        status=PaymentStatus.SUCCEEDED,
        failure_reason=None,
        occurred_at=datetime.now(UTC),
    )
    session.add(payment)
    await session.flush()

    session.add(
        IngestionEvent(
            idempotency_key=f"arr-sim:{action_id}:{attempt_no}",
            event_type="payment.succeeded",
            source="simulated_payment_provider",
            payload={
                "simulated": True,
                "action_id": str(action_id),
                "attempt_no": attempt_no,
                "case_id": str(case.id),
            },
            occurred_at=payment.occurred_at,
            customer_id=case.customer_id,
            payment_id=payment.id,
        )
    )
    await outbox_publisher.publish(
        session,
        DomainEvent(
            event_type="recovery_action.simulated_payment_succeeded",
            aggregate_id=case.id,
            aggregate_type="recovery_case",
            payload={
                "action_id": str(action_id),
                "attempt_no": attempt_no,
                "payment_id": str(payment.id),
                "simulated_reference": simulated_reference,
            },
        ),
    )
    return payment.id


async def execute_action(
    session: AsyncSession, case_id: UUID
) -> tuple[RecoveryCase, RecoveryAction, RecoveryActionExecution, bool]:
    """Execute (or attempt the next retry of) the scheduled action for a
    case's current decision.

    Returns ``(case, action, execution, created)``. ``created=False`` is
    returned both for a true idempotent replay (the action is already
    terminal -- ``RecoveryActionStatus.EXECUTED``, whether or not the
    attempt succeeded, see below) and for a call that lost a concurrent
    race for the current attempt number (see below); in both cases
    ``execution`` is the latest persisted attempt, never a fabricated one.

    ``NO_ACTION`` and ``MANUAL_REVIEW`` are always safe, first-class,
    single-attempt completions with
    :data:`~app.models.action.ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED`
    -- never treated as an error, never dispatched to a simulated executor.

    Every other approved strategy (``retry``,
    ``request_payment_method_update``, ``contact_customer``) dispatches to
    its ``app.decision.executors`` entry, which calls the deterministic,
    explicitly SIMULATED provider (``app.decision.providers`` -- no real
    external system is ever contacted). Up to :data:`RETRY_CAP` attempts
    are allowed per action:

    In every terminal case, ``RecoveryAction.status`` becomes ``executed``
    -- it means "the execution process completed", the pre-existing
    contract ``app.recovery.preconditions._requires_executed_action``
    checks for the ``action_executed -> observing`` transition, never
    "the attempt succeeded". Whether it succeeded lives entirely on the
    execution row's own ``outcome`` field, which Phase 7 reads honestly
    (a failed attempt simply leaves no resulting evidence for it to find):

    * A :data:`~app.decision.providers.SimulationOutcome.SUCCESS` attempt
      is immediately terminal: the case advances to ``action_executed``,
      and a new simulated ``payment.succeeded`` row is created (see
      :func:`_record_simulated_success`) with
      ``RecoveryActionExecution.resulting_payment_id`` set to it.
    * A :data:`~app.decision.providers.SimulationOutcome.PERMANENT_FAILURE`
      attempt is immediately terminal: the case still advances to
      ``action_executed`` (the execution *process* completed; Phase 7
      will separately observe no recovery evidence), but no payment is
      created and ``resulting_payment_id`` stays ``None``.
    * A :data:`~app.decision.providers.SimulationOutcome.TEMPORARY_FAILURE`
      attempt is terminal only once :data:`RETRY_CAP` attempts have been
      made; otherwise the action remains ``scheduled`` and a further call
      to this function attempts the next attempt. This never retries
      indefinitely and never bypasses this module's own
      idempotency/concurrency guarantees -- see the module docstring.

    Concurrency: a race for the SAME attempt number resolves via the
    ``(action_id, attempt_no)`` database unique constraint, exactly like
    every prior phase's KI-008-safe pattern. A caller that loses that race
    does not itself attempt a further retry within the same call --  it
    returns the current, just-committed state (``created=False``), same
    as a true idempotent replay. This is a deliberate, bounded resolution
    (never a retry loop inside a single request).

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case, :class:`NoScheduledActionError` if no action has been
    scheduled for this case's current decision (defensive), and
    :class:`CaseNotExecutableError` if the case is not in
    ``action_scheduled`` and the action is not already terminal.
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    action = await _get_locked_action_for_case(session, case_id)
    if action is None:
        raise NoScheduledActionError(case_id)

    if action.status != _Status.SCHEDULED.value:
        # Already terminal (executed or failed) -- idempotent replay of the
        # final result, regardless of case.state (mirrors
        # app.outcome.service.observe_outcome's own idempotent-replay
        # precedent: a repeat call after the case has moved on must still
        # succeed as a no-op).
        latest = _latest_execution(action)
        assert latest is not None  # a terminal action always has >=1 execution
        return case, action, latest, False

    if case.state not in _EXECUTABLE_FROM:
        raise CaseNotExecutableError(case.state.value)

    # Captured now, before any operation below can call rollback() -- same
    # MissingGreenlet hazard, and the same fix, as schedule_action above
    # and app.decision.service.decide_case: rollback() expires every
    # attribute on every ORM object attached to this session, so a later
    # plain `action.id` / `action.action_type` / `case.customer_id` read
    # would be an expired-attribute access. Everything below uses these
    # captured locals, never the ORM attributes again.
    action_id = action.id
    action_type = action.action_type
    case_id_value = case.id
    next_attempt_no = len(action.executions) + 1

    terminal_status: _Status | None
    #: The case-transition target once this attempt is terminal --
    #: ACTION_EXECUTED for every action type except manual_review (Phase
    #: 17), which blocks in PENDING_MANUAL_REVIEW for a human instead.
    terminal_case_state: RecoveryCaseState = RecoveryCaseState.ACTION_EXECUTED
    try:
        if action_type in _NO_SIDE_EFFECT_ACTION_TYPES:
            outcome_value = ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED.value
            execution = RecoveryActionExecution(
                action_id=action_id,
                attempt_no=next_attempt_no,
                idempotency_key=f"arr:{case_id_value}:{action_type}:{next_attempt_no}",
                outcome=outcome_value,
            )
            terminal_status = _Status.EXECUTED
        elif action_type == _MANUAL_REVIEW_ACTION_TYPE:
            # Deliberately NOT a completion (unlike no_action above): no
            # external system and no human has acted yet. This attempt
            # itself is still terminal (the action's own execution
            # process is done -- there is nothing further for
            # execute_action itself to do; a human resolves the case
            # separately via
            # app.recovery.manual_review.resolve_manual_review, which
            # never calls this function again), but the CASE transitions
            # into PENDING_MANUAL_REVIEW instead of ACTION_EXECUTED so a
            # human is genuinely required before the case can close.
            outcome_value = ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED.value
            execution = RecoveryActionExecution(
                action_id=action_id,
                attempt_no=next_attempt_no,
                idempotency_key=f"arr:{case_id_value}:{action_type}:{next_attempt_no}",
                outcome=outcome_value,
            )
            terminal_status = _Status.EXECUTED
            terminal_case_state = RecoveryCaseState.PENDING_MANUAL_REVIEW
        else:
            executor = EXECUTORS_BY_ACTION_TYPE[action_type]
            payment = await session.get(Payment, case.payment_id)
            assert payment is not None
            result = await executor.attempt(
                failure_reason=payment.failure_reason,
                attempt_no=next_attempt_no,
                correlation_id=str(action_id),
            )
            outcome_table = (
                _REAL_OUTCOME_BY_RESULT if result.is_real else _SIMULATED_OUTCOME_BY_RESULT
            )
            outcome_value = outcome_table[result.outcome].value

            resulting_payment_id: UUID | None = None
            if result.outcome is SimulationOutcome.SUCCESS:
                resulting_payment_id = await _record_simulated_success(
                    session,
                    case=case,
                    action_id=action_id,
                    attempt_no=next_attempt_no,
                    simulated_reference=result.simulated_reference,
                )
                terminal_status = _Status.EXECUTED
            elif result.outcome is SimulationOutcome.PERMANENT_FAILURE:
                # RecoveryAction.status stays EXECUTED, not FAILED, even
                # though the attempt did not succeed: status means "the
                # execution process completed" (the pre-existing contract
                # app.recovery.preconditions._requires_executed_action
                # checks for ACTION_EXECUTED -> OBSERVING), never "the
                # attempt was a success" -- that distinction lives on the
                # execution row's own `outcome` field
                # (SIMULATED_PERMANENT_FAILURE), which Phase 7 reads
                # honestly by finding no resulting evidence, not by this
                # module inventing a second, competing status vocabulary.
                terminal_status = _Status.EXECUTED
            elif next_attempt_no >= RETRY_CAP:
                terminal_status = _Status.EXECUTED  # cap exhausted -- same reasoning as above
            else:
                terminal_status = None  # temporary failure, attempts remain

            execution = RecoveryActionExecution(
                action_id=action_id,
                attempt_no=next_attempt_no,
                idempotency_key=f"arr:{case_id_value}:{action_type}:{next_attempt_no}",
                outcome=outcome_value,
                detail=result.detail,
                simulated_reference=result.simulated_reference,
                resulting_payment_id=resulting_payment_id,
            )
        # Append to the already-loaded relationship (action.executions was
        # eager-loaded by get_action_for_case above) rather than a bare
        # session.add: this keeps action.executions accurate in memory for
        # the ActionOut this function returns -- expire_on_commit=False
        # means the collection would otherwise stay stale after the
        # commit below. The append also schedules the insert via the
        # relationship's default save-update cascade.
        action.executions.append(execution)
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Same KI-008-safe recheck pattern as schedule_action /
        # decide_case: the database's unique constraint on
        # (action_id, attempt_no) -- or, for a SUCCESS attempt, on the
        # simulated Payment's external_reference, which embeds the same
        # (action_id, attempt_no) -- is authoritative, not this branch.
        # rollback() expires EVERY attribute on EVERY ORM object attached
        # to this session -- not just the ones a partial `refresh(...,
        # attribute_names=[...])` call targets. A plain `action.action_type`
        # / `action.created_at` read during the ActionOut serialization
        # below would then be an expired-attribute access -- implicit
        # lazy-load IO from synchronous code, which raises MissingGreenlet
        # under asyncio (confirmed by
        # test_concurrent_execute_requests_produce_exactly_one_execution:
        # a partial refresh reproduces it, a fresh query does not). A
        # brand-new query via get_action_for_case -- never reusing the
        # stale, partially-refreshed `action` object -- is the same fix
        # every other module in this codebase already uses for this exact
        # hazard (app.decision.service.decide_case, this module's own
        # schedule_action). Uses the captured case_id, never case.id.
        current_case = await recovery_service.get_case(session, case_id)
        current_action = await get_action_for_case(session, case_id)
        if current_action is None:
            raise NoScheduledActionError(case_id) from None
        latest = _latest_execution(current_action)
        if latest is not None:
            return current_case, current_action, latest, False
        raise

    if terminal_status is not None:
        action.status = terminal_status.value
        try:
            updated_case = await recovery_service.transition_case(
                session,
                case_id,
                terminal_case_state,
                actor=_EXECUTE_ACTOR,
                reason=f"action executed: {action_type} ({outcome_value})",
                enforce_preconditions=True,
            )
        except Exception:
            await session.rollback()
            raise
    else:
        # Temporary failure, attempts remain: persist this attempt but do
        # not transition the case -- it stays action_scheduled, and a
        # further execute_action call attempts the next attempt.
        await session.commit()
        updated_case = case

    logger.info(
        "action executed" if terminal_status is not None else "action attempt recorded",
        extra={
            "case_id": str(updated_case.id),
            "action_id": str(action_id),
            "execution_id": str(execution.id),
            "action_type": action_type,
            "attempt_no": next_attempt_no,
            "execution_outcome": outcome_value,
            "terminal": terminal_status is not None,
        },
    )
    return updated_case, action, execution, True


__all__ = [
    "execute_action",
    "get_action_for_case",
    "schedule_action",
]
