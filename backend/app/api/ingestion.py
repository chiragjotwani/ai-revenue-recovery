from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PaymentReferenceConflictError
from app.db.session import get_db_session
from app.schemas.ingestion import IngestionResult, PaymentEventIn
from app.services.ingestion import ingest_payment_event

router = APIRouter(prefix="/events", tags=["ingestion"])


@router.post("", response_model=IngestionResult, status_code=status.HTTP_201_CREATED)
async def post_payment_event(
    event_in: PaymentEventIn,
    session: AsyncSession = Depends(get_db_session),
) -> IngestionResult:
    try:
        return await ingest_payment_event(session, event_in)
    except PaymentReferenceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
