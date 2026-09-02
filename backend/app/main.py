from fastapi import FastAPI

from app.api.analytics import router as analytics_router
from app.api.health import router as health_router
from app.api.ingestion import router as ingestion_router
from app.api.measurement import router as measurement_router
from app.api.recovery import router as recovery_router
from app.api.risk import router as risk_router
from app.core.config import get_settings
from app.core.windows_compat import apply_windows_event_loop_policy

apply_windows_event_loop_policy()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Revenue Recovery Platform",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
    )
    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(risk_router)
    app.include_router(recovery_router)
    app.include_router(measurement_router)
    app.include_router(analytics_router)
    return app


app = create_app()
