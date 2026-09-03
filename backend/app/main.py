from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.ai_models import router as ai_models_router
from app.api.analytics import router as analytics_router
from app.api.health import router as health_router
from app.api.ingestion import router as ingestion_router
from app.api.measurement import router as measurement_router
from app.api.observability import router as observability_router
from app.api.recovery import router as recovery_router
from app.api.risk import router as risk_router
from app.api.warehouse import router as warehouse_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.windows_compat import apply_windows_event_loop_policy

apply_windows_event_loop_policy()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Reapplied here, not only at module-import time below: uvicorn
    # applies its OWN default logging config during server startup, which
    # runs after this module is imported but before the ASGI lifespan
    # "startup" phase -- reconfiguring here, last, is what makes this
    # app's JSON formatter (app.core.logging) actually win in production
    # instead of being silently clobbered by uvicorn's own dictConfig.
    # configure_logging() is idempotent, so this is safe to call twice.
    # (The import-time call below still matters on its own: test clients
    # built on httpx.ASGITransport never run the ASGI lifespan protocol
    # at all, so it is the only configuration they get.)
    configure_logging(get_settings().log_level)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="AI Revenue Recovery Platform",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        lifespan=_lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(observability_router)
    app.include_router(ingestion_router)
    app.include_router(risk_router)
    app.include_router(recovery_router)
    app.include_router(measurement_router)
    app.include_router(analytics_router)
    app.include_router(warehouse_router)
    app.include_router(ai_models_router)
    return app


app = create_app()
