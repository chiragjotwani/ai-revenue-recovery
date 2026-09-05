"""Metrics exposition (Phase 14).

``GET /metrics`` in the standard Prometheus text format. Operational
series (``app.core.metrics.HTTP_REQUESTS_TOTAL`` etc.) are already live
counters/histograms updated by ``app.core.middleware`` and the Phase 12
event pipeline; business gauges are refreshed from the Phase 8/13
reports right before each scrape, so they never go stale between
scrapes and never require a separate background updater.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import (
    OBSERVED_RECOVERED_REVENUE,
    RECOVERY_ATTEMPTS_TOTAL_GAUGE,
    REVENUE_AT_RISK,
)
from app.db.session import get_db_session
from app.warehouse.service import get_analytics_warehouse_report

router = APIRouter(tags=["observability"])


async def _refresh_business_gauges(session: AsyncSession) -> None:
    report = await get_analytics_warehouse_report(session)

    REVENUE_AT_RISK.clear()
    for entry in report.revenue_at_risk:
        REVENUE_AT_RISK.labels(currency=entry.currency).set(float(entry.amount))

    OBSERVED_RECOVERED_REVENUE.clear()
    for entry in report.observed_recovered:
        OBSERVED_RECOVERED_REVENUE.labels(currency=entry.currency).set(float(entry.amount))

    RECOVERY_ATTEMPTS_TOTAL_GAUGE.set(report.total_recovery_attempts)


@router.get("/metrics")
async def get_metrics(session: AsyncSession = Depends(get_db_session)) -> Response:
    await _refresh_business_gauges(session)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
