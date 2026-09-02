from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.measurement.schema import RevenueReport
from app.measurement.service import get_revenue_report

router = APIRouter(prefix="/measurement", tags=["measurement"])


@router.get("/report", response_model=RevenueReport)
async def get_report(session: AsyncSession = Depends(get_db_session)) -> RevenueReport:
    """The Phase 8 revenue measurement report: observed at-risk/recovered/
    not-recovered/unresolved value, per currency (KI-006 -- never summed
    across currencies), plus strategy/disposition breakdowns of observed
    recovered value.

    Every number here is OBSERVED (a later successful/failed payment
    exists as evidence), never a causal or incremental estimate -- see
    ``RevenueReport.counterfactual_limitation``. This mirrors
    ``GET /risk/summary``'s own live-computation shape.
    """
    return await get_revenue_report(session)
