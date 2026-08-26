from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Liveness check. Must not depend on external services.

    Readiness checks that verify database/Redis connectivity are
    introduced in Phase 1 once the data layer exists.
    """
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)
