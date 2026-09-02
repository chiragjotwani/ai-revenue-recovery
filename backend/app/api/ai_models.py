from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.factory import select_reasoning_model
from app.ai.report import get_model_report
from app.db.session import get_db_session
from app.schemas.ai import ModelReportOut, ProviderStatusOut

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/providers", response_model=ProviderStatusOut)
async def get_provider_status() -> ProviderStatusOut:
    """The Phase 10 model router's current resolution -- what provider was
    requested, what was actually resolved, and why they differ (if they
    do). Read-only; makes a config-time substitution observable without
    waiting for a diagnosis to complete (see KI-009).
    """
    selection = select_reasoning_model()
    return ProviderStatusOut(
        requested_provider=selection.requested_provider,
        resolved_provider=selection.resolved_provider,
        substituted=selection.substituted,
        substitution_reason=selection.substitution_reason,
    )


@router.get("/model-report", response_model=ModelReportOut)
async def get_model_usage_report(
    session: AsyncSession = Depends(get_db_session),
) -> ModelReportOut:
    """Real, recorded per-model usage (diagnosis count, mean latency, mean
    confidence, escalation count) -- computed from actually-persisted
    ``Diagnosis`` rows, never from synthetic evaluation data (KI-007).
    """
    return await get_model_report(session)
