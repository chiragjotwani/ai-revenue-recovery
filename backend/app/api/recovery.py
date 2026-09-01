from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.diagnosis import DiagnosisValidationError
from app.ai.providers.base import ReasoningModelError
from app.core.errors import (
    CaseNotDecidableError,
    CaseNotDiagnosableError,
    CaseNotExecutableError,
    CaseNotSchedulableError,
    DecisionNotApprovedError,
    IllegalStateTransitionError,
    NoApprovedDecisionError,
    NoDiagnosisToDecideError,
    NoScheduledActionError,
    PaymentNotFoundError,
    PaymentNotRecoverableError,
    RecoveryCaseNotFoundError,
    TransitionPreconditionError,
)
from app.db.session import get_db_session
from app.decision.actions import execute_action, get_action_for_case, schedule_action
from app.decision.service import decide_case, get_decision_for_case
from app.models.recovery import RecoveryCaseState
from app.recovery import service
from app.schemas.recovery import (
    ActionOut,
    DecisionOut,
    DiagnosisOut,
    OpenCaseRequest,
    RecoveryCaseDetail,
    RecoveryCaseOut,
    TransitionRequest,
)
from app.services.diagnosis import diagnose_case, get_latest_diagnosis

router = APIRouter(prefix="/recovery", tags=["recovery"])


@router.post(
    "/cases",
    response_model=RecoveryCaseOut,
    status_code=status.HTTP_201_CREATED,
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


@router.get("/cases", response_model=list[RecoveryCaseOut])
async def list_recovery_cases(
    state: RecoveryCaseState | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[RecoveryCaseOut]:
    cases = await service.list_cases(session, state=state)
    return [RecoveryCaseOut.model_validate(c) for c in cases]


@router.get("/cases/{case_id}", response_model=RecoveryCaseDetail)
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
        }
    )


@router.post("/cases/{case_id}/transitions", response_model=RecoveryCaseOut)
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


@router.post("/cases/{case_id}/diagnose", response_model=DiagnosisOut)
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


@router.post("/cases/{case_id}/decide", response_model=DecisionOut)
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


@router.post("/cases/{case_id}/schedule-action", response_model=ActionOut)
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


@router.post("/cases/{case_id}/execute-action", response_model=ActionOut)
async def execute_recovery_action(
    case_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ActionOut:
    """Execute the scheduled action for a case's current decision (Phase
    6). ``no_action``/``manual_review`` complete with no external side
    effect; every other approved strategy is recorded as deferred -- no
    payment-provider or messaging integration exists in this repository
    (see ``app.decision.actions`` module docstring).

    Advances the case ``action_scheduled -> action_executed``.

    Idempotent on ``(action_id, attempt_no=1)``: a repeat call returns the
    same persisted execution rather than creating a second attempt or a
    second external effect.

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
