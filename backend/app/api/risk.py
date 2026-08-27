from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.risk.service import get_risk_summary, list_risk_assessments
from app.schemas.risk import RiskAssessment, RiskSummary

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/payments", response_model=list[RiskAssessment])
async def get_at_risk_payments(
    session: AsyncSession = Depends(get_db_session),
) -> list[RiskAssessment]:
    return await list_risk_assessments(session)


@router.get("/summary", response_model=RiskSummary)
async def get_summary(session: AsyncSession = Depends(get_db_session)) -> RiskSummary:
    return await get_risk_summary(session)
