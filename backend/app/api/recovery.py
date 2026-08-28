from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.diagnosis import DiagnosisValidationError
from app.ai.providers.base import ReasoningModelError
from app.core.errors import (
    CaseNotDiagnosableError,
    IllegalStateTransitionError,
    PaymentNotFoundError,
    PaymentNotRecoverableError,
    RecoveryCaseNotFoundError,
)
from app.db.session import get_db_session
from app.models.recovery import RecoveryCaseState
from app.recovery import service
from app.schemas.recovery import (
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
