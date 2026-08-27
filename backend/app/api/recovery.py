from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    IllegalStateTransitionError,
    PaymentNotFoundError,
    PaymentNotRecoverableError,
    RecoveryCaseNotFoundError,
)
from app.db.session import get_db_session
from app.models.recovery import RecoveryCaseState
from app.recovery import service
from app.schemas.recovery import (
    OpenCaseRequest,
    RecoveryCaseDetail,
    RecoveryCaseOut,
    TransitionRequest,
)

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
    return RecoveryCaseDetail.model_validate(
        {
            "id": case.id,
            "payment_id": case.payment_id,
            "customer_id": case.customer_id,
            "state": case.state,
            "opened_at": case.opened_at,
            "closed_at": case.closed_at,
            "history": list(history),
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
