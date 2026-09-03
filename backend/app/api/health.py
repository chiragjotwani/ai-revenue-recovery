import asyncio

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session

router = APIRouter(tags=["health"])

_KAFKA_PROBE_TIMEOUT_SECONDS = 2.0


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


async def _probe_kafka(bootstrap_servers: str | None) -> str:
    if not bootstrap_servers:
        return "not_configured"
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
    try:
        await asyncio.wait_for(producer.start(), timeout=_KAFKA_PROBE_TIMEOUT_SECONDS)
    except (KafkaError, TimeoutError, OSError) as exc:
        return f"error: {type(exc).__name__}"
    else:
        return "ok"
    finally:
        await producer.stop()


@router.get("/health/ready", response_model=ReadinessResponse)
async def get_readiness(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> ReadinessResponse:
    """Readiness check: verifies the dependencies the application actually
    uses at runtime.

    PostgreSQL is REQUIRED -- every request path needs it, so a failed
    check here flips overall ``status`` to ``not_ready`` and returns
    ``503``. Kafka (Phase 12) is reported for visibility but never flips
    readiness: per ADR-007, the outbox pattern means a Kafka outage never
    blocks request-serving (it only stalls ``scripts/event_relay.py``,
    a separate process), so reporting the backend as "not ready" because
    Kafka is unreachable would be readiness lying in the OTHER direction
    -- exactly what this endpoint's own prior docstring already warned
    against for Redis. Redis remains unprobed for the same original
    reason: no application code path uses it yet.
    """
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, don't crash the probe
        checks["database"] = f"error: {type(exc).__name__}"

    checks["kafka"] = await _probe_kafka(get_settings().kafka_bootstrap_servers)

    ready = checks["database"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
