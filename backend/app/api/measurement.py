from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.measurement.baseline import get_baseline_comparison_report
from app.measurement.schema import BaselineComparisonReport, RevenueReport
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


@router.get("/baseline-comparison", response_model=BaselineComparisonReport)
async def get_baseline_comparison(
    session: AsyncSession = Depends(get_db_session),
) -> BaselineComparisonReport:
    """Baseline ('blind retry', simulated) vs. AI-gated (real, observed)
    recovery over the same eligible case population -- see
    ``BaselineComparisonReport.methodology``. NOT a randomized control/
    treatment experiment; NOT a causal or incremental-lift estimate.
    """
    return await get_baseline_comparison_report(session)
