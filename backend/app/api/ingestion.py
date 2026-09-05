from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Role, require_role
from app.core.errors import PaymentReferenceConflictError
from app.db.session import get_db_session
from app.schemas.ingestion import IngestionResult, PaymentEventIn
from app.services.ingestion import ingest_payment_event

router = APIRouter(prefix="/events", tags=["ingestion"])


@router.post(
    "",
    response_model=IngestionResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_role(Role.OPERATOR)],
)
async def post_payment_event(
    event_in: PaymentEventIn,
    session: AsyncSession = Depends(get_db_session),
) -> IngestionResult:
    try:
        return await ingest_payment_event(session, event_in)
    except PaymentReferenceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DataError as exc:
        # Backstop only: request-shape validation lives in the schema
        # (app/schemas/ingestion.py). A DataError here means a value slipped
        # past that contract and the database rejected it -- surface it as a
        # 422, never a 500, and never leak the SQL.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a field value is outside the range the platform accepts",
        ) from exc
