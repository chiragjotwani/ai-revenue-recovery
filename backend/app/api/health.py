from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str  # "ready" | "not_ready"
    checks: dict[str, str]  # dependency name -> "ok" or "error: <detail>"


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Liveness check. Does not touch external services -- a process that
    can answer this is running. Use ``GET /health/ready`` to check whether
    the dependencies the application actually needs are reachable.
    """
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)


@router.get("/health/ready", response_model=ReadinessResponse)
async def get_readiness(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> ReadinessResponse:
    """Readiness check: verifies the dependencies the application actually
    uses at runtime.

    Today that is PostgreSQL only. Redis is declared in configuration and
    started by docker-compose but no application code path uses it yet
    (background workers arrive in a later phase), so it is deliberately not
    probed here -- probing a dependency the app does not use would make
    readiness lie in the other direction. Returns ``503`` if a required
    dependency is unreachable.
    """
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, don't crash the probe
        checks["database"] = f"error: {type(exc).__name__}"

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
