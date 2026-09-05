from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Role, require_role
from app.db.session import get_db_session
from app.warehouse.etl import rebuild_warehouse
from app.warehouse.schema import AnalyticsWarehouseReport, CaseAnalyticsFactRow
from app.warehouse.service import get_analytics_warehouse_report, get_fact_rows

router = APIRouter(prefix="/analytics/warehouse", tags=["analytics"])


class RebuildResult(BaseModel):
    model_config = {"extra": "forbid"}

    facts_written: int


@router.post("/rebuild", response_model=RebuildResult, dependencies=[require_role(Role.OPERATOR)])
async def rebuild(session: AsyncSession = Depends(get_db_session)) -> RebuildResult:
    """Recompute every case's analytics fact row from the current
    operational data (Phase 13). Idempotent -- safe to call repeatedly.
    The same operation ``scripts/build_analytics_warehouse.py`` runs from
    the command line; exposed here too so an on-demand refresh (or a
    test) does not need shell access to the backend process.
    """
    result = await rebuild_warehouse(session)
    return RebuildResult(facts_written=result.facts_written)


@router.get(
    "/facts", response_model=list[CaseAnalyticsFactRow], dependencies=[require_role(Role.READONLY)]
)
async def get_facts(
    session: AsyncSession = Depends(get_db_session),
) -> list[CaseAnalyticsFactRow]:
    """The raw analytics-warehouse materialization (Phase 13): one row
    per case, as of the last ``scripts/build_analytics_warehouse.py``
    run. Empty if the warehouse has never been built.
    """
    return await get_fact_rows(session)


@router.get(
    "/report", response_model=AnalyticsWarehouseReport, dependencies=[require_role(Role.READONLY)]
)
async def get_report(
    session: AsyncSession = Depends(get_db_session),
) -> AnalyticsWarehouseReport:
    """The full Phase 13 analytics warehouse report: revenue at risk,
    observed recovered revenue, natural-vs-attempted recovery split,
    recovery attempts, strategy/failure-reason/customer-segment recovery
    rates, and model performance -- computed from the pre-built warehouse
    materialization, not live from the operational tables. See
    ``AnalyticsWarehouseReport.experiment_limitation`` for what is
    deliberately not computed and why.
    """
    return await get_analytics_warehouse_report(session)
