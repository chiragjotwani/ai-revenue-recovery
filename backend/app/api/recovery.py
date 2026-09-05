from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.diagnosis import DiagnosisValidationError
from app.ai.providers.base import ReasoningModelError
from app.core.auth import Role, require_role
from app.core.errors import (
    CaseNotDecidableError,
    CaseNotDiagnosableError,
    CaseNotExecutableError,
    CaseNotMeasurableError,
    CaseNotObservableError,
    CaseNotPendingManualReviewError,
    CaseNotSchedulableError,
    DecisionNotApprovedError,
    IllegalStateTransitionError,
    ManualReviewAlreadyResolvedError,
    NoApprovedDecisionError,
    NoDiagnosisToDecideError,
    NoExecutedActionError,
    NoScheduledActionError,
    PaymentNotFoundError,
    PaymentNotRecoverableError,
    RecoveryCaseNotFoundError,
    TransitionPreconditionError,
)
from app.db.session import get_db_session
from app.decision.actions import execute_action, get_action_for_case, schedule_action
from app.decision.service import decide_case, get_decision_for_case
from app.measurement.service import get_measurement_for_case, measure_case
from app.models.recovery import RecoveryCaseState
from app.outcome.service import get_outcome_for_case, observe_outcome
from app.recovery import service
from app.recovery.manual_review import get_manual_review_resolution, resolve_manual_review
from app.retrieval.schema import SimilarCase
from app.retrieval.service import NoFeaturesAvailableError, find_similar_cases
from app.schemas.recovery import (
    ActionOut,
    DecisionOut,
    DiagnosisOut,
    ManualReviewResolutionOut,
    MeasurementOut,
    OpenCaseRequest,
    OutcomeOut,
    RecoveryCaseDetail,
    RecoveryCaseOut,
    ResolveManualReviewRequest,
    TransitionRequest,
)
from app.services.diagnosis import diagnose_case, get_latest_diagnosis

router = APIRouter(prefix="/recovery", tags=["recovery"])


@router.post(
    "/cases",
    response_model=RecoveryCaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_role(Role.OPERATOR)],
)
async def open_recovery_case(
    body: OpenCaseRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> RecoveryCaseOut:
    """Open a recovery case for a failed payment.

    Idempotent on the payment: a repeat call returns the existing case with
    ``200 OK`` instead of creating a second (``201 Created`` only on first
    open).
    """
    try:
        case, created = await service.open_case(session, body.payment_id, actor="api")
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PaymentNotRecoverableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if not created:
        response.status_code = status.HTTP_200_OK
    return RecoveryCaseOut.model_validate(case)


@router.get(
    "/cases", response_model=list[RecoveryCaseOut], dependencies=[require_role(Role.READONLY)]
)
async def list_recovery_cases(
    state: RecoveryCaseState | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[RecoveryCaseOut]:
    cases = await service.list_cases(session, state=state)
    return [RecoveryCaseOut.model_validate(c) for c in cases]


@router.get(
    "/cases/{case_id}",
    response_model=RecoveryCaseDetail,
    dependencies=[require_role(Role.READONLY)],
)
async def get_recovery_case(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> RecoveryCaseDetail:
    try:
        case = await service.get_case(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    history = await service.get_case_transitions(session, case_id)
    latest_diagnosis = await get_latest_diagnosis(session, case_id)
    decision = await get_decision_for_case(session, case_id)
    action = await get_action_for_case(session, case_id)
    outcome = await get_outcome_for_case(session, case_id)
    measurement = await get_measurement_for_case(session, case_id)
    manual_review_resolution = await get_manual_review_resolution(session, case_id)
    return RecoveryCaseDetail.model_validate(
        {
            "id": case.id,
            "payment_id": case.payment_id,
            "customer_id": case.customer_id,
            "state": case.state,
            "opened_at": case.opened_at,
            "closed_at": case.closed_at,
            "history": list(history),
            "diagnosis": (
                DiagnosisOut.model_validate(latest_diagnosis)
                if latest_diagnosis is not None
                else None
            ),
            "decision": (DecisionOut.model_validate(decision) if decision is not None else None),
            "action": (ActionOut.model_validate(action) if action is not None else None),
            "outcome": (OutcomeOut.model_validate(outcome) if outcome is not None else None),
            "measurement": (
                MeasurementOut.model_validate(measurement) if measurement is not None else None
            ),
            "manual_review_resolution": (
                ManualReviewResolutionOut.model_validate(manual_review_resolution)
                if manual_review_resolution is not None
                else None
            ),
        }
    )


@router.post(
    "/cases/{case_id}/transitions",
    response_model=RecoveryCaseOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def transition_recovery_case(
    case_id: UUID,
    body: TransitionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> RecoveryCaseOut:
    try:
        case = await service.transition_case(
            session, case_id, body.to_state, actor="api", reason=body.reason
        )
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IllegalStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return RecoveryCaseOut.model_validate(case)


@router.post(
    "/cases/{case_id}/diagnose",
    response_model=DiagnosisOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def diagnose_recovery_case(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> DiagnosisOut:
    """Run the configured reasoning model to diagnose a case's payment failure.

    Advances the case ``detected -> diagnosing -> diagnosed`` and stores the
    diagnosis. The model only diagnoses; it never decides or acts (ADR-003).

    - ``404`` unknown case.
    - ``409`` the case is not in ``detected``/``diagnosing``.
    - ``502`` the model was unreachable or its output could not be
      validated; the case is left in ``diagnosing`` and can be retried.
    """
    try:
        _case, row = await diagnose_case(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CaseNotDiagnosableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ReasoningModelError, DiagnosisValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"diagnosis could not be completed: {exc}",
        ) from exc

    return DiagnosisOut.model_validate(row)


@router.post(
    "/cases/{case_id}/decide",
    response_model=DecisionOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def decide_recovery_case(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> DecisionOut:
    """Run the deterministic Phase 5 policy engine against a case's
    diagnosis and persist the decision.

    Advances the case ``diagnosed -> decision_pending``. Never calls an AI
    provider and never executes a recovery action (Phase 5 Architecture
    Revision) -- an ``escalated`` or ``rejected`` decision status is a
    successful, valid decision result, not an error: it is returned with
    ``200 OK`` like any other decision.

    Idempotent on ``(case_id, diagnosis_id)``: a repeat call for a case
    that has already been decided against its current diagnosis returns
    the same persisted decision rather than raising or creating a second
    one (``app.decision.service.decide_case``'s own idempotency, backed by
    a database unique constraint -- KI-008). This endpoint adds no
    second, API-level idempotency mechanism of its own.

    - ``404`` unknown case.
    - ``409`` the case is not in ``diagnosed`` (and has no existing
      decision to replay).
    - ``500`` a defensive invariant was violated (e.g. a ``diagnosed``
      case somehow has no persisted diagnosis, or the decision engine's
      own just-flushed row failed its precondition check) -- this should
      not occur under the normal state machine and is not a condition a
      client caused or can resolve by retrying with different input.
    """
    try:
        _case, row = await decide_case(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CaseNotDecidableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (NoDiagnosisToDecideError, TransitionPreconditionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="the case could not be decided due to an unexpected internal state",
        ) from exc

    return DecisionOut.model_validate(row)


@router.post(
    "/cases/{case_id}/schedule-action",
    response_model=ActionOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def schedule_recovery_action(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ActionOut:
    """Schedule the action for a case's current, policy-approved decision
    (Phase 6). Never accepts a strategy from the caller -- the action type
    is always the decision's own ``approved_strategy`` (ADR-003).

    Advances the case ``decision_pending -> action_scheduled``.

    Idempotent on ``(case_id, action_type, decision_result_id)``: a repeat
    call for a case whose action already exists returns the same
    persisted row rather than raising or creating a second one
    (``app.decision.actions.schedule_action``'s own idempotency, backed by
    a database unique constraint -- KI-008).

    - ``404`` unknown case.
    - ``409`` the case is not in ``decision_pending`` (and has no existing
      action to replay), or its decision is not ``approved`` (an
      escalated or rejected decision can never be scheduled).
    - ``500`` a defensive invariant was violated (e.g. a
      ``decision_pending`` case somehow has no persisted decision) -- not
      a condition a client caused or can resolve by retrying.
    """
    try:
        _case, row, _created = await schedule_action(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (CaseNotSchedulableError, DecisionNotApprovedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (NoApprovedDecisionError, TransitionPreconditionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="the action could not be scheduled due to an unexpected internal state",
        ) from exc

    return ActionOut.model_validate(row)


@router.post(
    "/cases/{case_id}/execute-action",
    response_model=ActionOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def execute_recovery_action(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ActionOut:
    """Execute (or attempt the next retry of) the scheduled action for a
    case's current decision (Phase 6). ``no_action`` completes immediately
    with no external side effect. ``manual_review`` also completes this
    action immediately (its own execution process needs no external
    system), but instead advances the CASE to
    ``pending_manual_review`` rather than ``action_executed`` -- an
    operator must resolve it via
    ``POST /recovery/cases/{id}/resolve-manual-review`` (Phase 17) before
    the case can close. Every other approved strategy runs against a
    provider -- ``retry`` against a real Stripe TEST-mode gateway when
    configured, otherwise (and always for
    ``request_payment_method_update``/``contact_customer``) a
    deterministic SIMULATED provider (see ``app.decision.actions`` /
    ``app.decision.providers`` / ``app.decision.providers_stripe`` module
    docstrings).

    Advances the case ``action_scheduled -> action_executed`` (or, for
    ``manual_review``, ``-> pending_manual_review``) once the action
    reaches a terminal outcome (success, permanent failure, or the
    bounded retry cap is exhausted). While a temporary failure has
    occurred and attempts remain, the case stays ``action_scheduled`` and
    calling this endpoint again attempts the next attempt -- it is not
    only idempotent, it is also how a bounded retry advances.

    Idempotent per attempt: a repeat call once the action is terminal
    returns the same persisted final execution rather than creating a new
    attempt or a second simulated effect.

    - ``404`` unknown case.
    - ``409`` the case is not in ``action_scheduled`` (and has no existing
      execution to replay).
    - ``500`` a defensive invariant was violated (e.g. an
      ``action_scheduled`` case somehow has no scheduled action) -- not a
      condition a client caused or can resolve by retrying.
    """
    try:
        _case, action, _execution, _created = await execute_action(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CaseNotExecutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (NoScheduledActionError, TransitionPreconditionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="the action could not be executed due to an unexpected internal state",
        ) from exc

    return ActionOut.model_validate(action)


@router.post(
    "/cases/{case_id}/resolve-manual-review",
    response_model=ManualReviewResolutionOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def resolve_recovery_case_manual_review(
    case_id: UUID,
    body: ResolveManualReviewRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ManualReviewResolutionOut:
    """An operator resolves a case's manual-review escalation (Phase 17).

    A case reaches ``pending_manual_review`` when the Phase 5 policy
    engine escalates a decision to ``manual_review`` (fraud suspicion,
    insufficient evidence, conflicting signals -- see
    ``app.decision.policy``) and that action has executed
    (``POST .../execute-action``). This is the only way such a case can
    ever leave that state: there is no automated re-decision loop and no
    automatic timeout.

    ``resolution`` is either ``abandoned`` (stop pursuing this case) or
    ``failed`` (the recovery attempt did not work) -- never ``recovered``:
    no authoritative payment evidence exists merely because a human
    looked at the case, and this endpoint never invokes Phase 7's
    evidence-based outcome observation. ``note`` is required (1-1000
    characters) -- an operator's reasoning is part of the permanent audit
    trail, the same discipline every other decision point in this system
    already records structurally.

    NOT idempotent: a case can only leave ``pending_manual_review`` once.
    A repeat call is a genuine conflict (``409``), never a replay.

    - ``404`` unknown case.
    - ``409`` the case is not in ``pending_manual_review``, or its manual
      review was already resolved (including a concurrent resolution
      that won a race against this one).
    """
    try:
        _case, row = await resolve_manual_review(
            session, case_id, resolution=body.resolution, note=body.note
        )
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (CaseNotPendingManualReviewError, ManualReviewAlreadyResolvedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return ManualReviewResolutionOut.model_validate(row)


@router.post(
    "/cases/{case_id}/observe-outcome",
    response_model=OutcomeOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def observe_recovery_outcome(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> OutcomeOut:
    """Observe the real-world outcome of a case's executed action, from
    authoritative payment evidence alone (Phase 7). Never calls an AI
    provider, never re-runs the Phase 5 policy engine, never executes
    another action, and never marks a case ``recovered`` merely because an
    action executed -- ``action_executed`` and ``recovered`` are kept
    strictly distinct (see ``app.outcome.service``).

    Advances the case ``action_executed -> observing``, and further to
    ``observing -> recovered`` only when a later successful payment for
    the same customer is found. ``not_recovered``/``unresolved`` leave the
    case in ``observing`` -- both are valid, non-error outcomes returned
    with ``200 OK``, exactly like an escalated/rejected Phase 5 decision.

    Idempotent: a repeat call with unchanged evidence returns the same
    persisted observation rather than creating a duplicate
    (``app.outcome.service.observe_outcome``'s own idempotency, backed by
    a database unique constraint -- KI-008). Genuinely new evidence
    (e.g. a payment succeeded since the last observation) creates a new,
    append-only observation attempt rather than overwriting the old one.

    - ``404`` unknown case.
    - ``409`` the case is not in ``action_executed``/``observing``.
    - ``500`` a defensive invariant was violated (e.g. an
      ``action_executed`` case somehow has no executed action) -- not a
      condition a client caused or can resolve by retrying.
    """
    try:
        _case, row, _created = await observe_outcome(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CaseNotObservableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (NoExecutedActionError, TransitionPreconditionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="the outcome could not be observed due to an unexpected internal state",
        ) from exc

    return OutcomeOut.model_validate(row)


@router.post(
    "/cases/{case_id}/measure",
    response_model=MeasurementOut,
    dependencies=[require_role(Role.OPERATOR)],
)
async def measure_recovery_case(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> MeasurementOut:
    """Measure a case's current, observed outcome (Phase 8). Accepts no
    request body -- there is no field through which a caller could supply
    an amount, currency, or status; every measured fact is read from the
    case's own Phase 7 outcome observation and its ``Payment`` row.

    Idempotent: a repeat call for a case whose outcome hasn't changed
    returns the same persisted measurement rather than creating a second
    one (``app.measurement.service.measure_case``'s own idempotency,
    backed by a database unique constraint -- KI-008). A case whose
    outcome later changes (new Phase 7 evidence) measures again into a
    new, append-only row on the next call.

    Never executes an action, never re-runs the Phase 5 policy engine,
    and never re-classifies the Phase 7 outcome -- this endpoint only
    records that a measurement was taken.

    - ``404`` unknown case.
    - ``409`` the case has no observed outcome yet (Phase 7's
      ``POST .../observe-outcome`` must run first).
    """
    try:
        row, _created = await measure_case(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CaseNotMeasurableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return MeasurementOut.model_validate(row)


@router.get(
    "/cases/{case_id}/similar-cases",
    response_model=list[SimilarCase],
    dependencies=[require_role(Role.READONLY)],
)
async def get_similar_recovery_cases(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[SimilarCase]:
    """The most similar historical cases to this one (Phase 11), ranked by
    deterministic structured-feature similarity -- NOT a learned/neural
    embedding, NOT a prediction of this case's outcome. Advisory
    information only: this endpoint never schedules or executes an
    action, never changes a decision, and never feeds anything back into
    the Phase 4 diagnosis pipeline.

    - ``404`` unknown case.
    - ``409`` the case has no diagnosis yet (nothing to compute features
      from).
    """
    try:
        return await find_similar_cases(session, case_id)
    except RecoveryCaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoFeaturesAvailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
