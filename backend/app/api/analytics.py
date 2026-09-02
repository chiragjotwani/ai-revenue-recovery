from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.schema import StrategyAnalyticsReport, StrategyDatasetRow
from app.analytics.service import get_strategy_analytics_report, get_strategy_dataset
from app.db.session import get_db_session

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/strategy-dataset", response_model=list[StrategyDatasetRow])
async def get_dataset(
    session: AsyncSession = Depends(get_db_session),
) -> list[StrategyDatasetRow]:
    """The historical strategy dataset (Phase 9): one row per case with an
    executed/scheduled action, its strategy, disposition, and current
    Phase 7 outcome (``null`` if not yet observed). Raw and auditable --
    no aggregation, no model, no prediction.
    """
    return await get_strategy_dataset(session)


@router.get("/strategy-report", response_model=StrategyAnalyticsReport)
async def get_report(
    session: AsyncSession = Depends(get_db_session),
) -> StrategyAnalyticsReport:
    """Empirical recovery-rate statistics by strategy and by disposition
    (Phase 9). Every rate is an observed frequency over a disclosed
    sample size, never a prediction -- see
    ``StrategyAnalyticsReport.ml_model_limitation`` for why no ML model or
    strategy optimizer exists in this repository.
    """
    return await get_strategy_analytics_report(session)
