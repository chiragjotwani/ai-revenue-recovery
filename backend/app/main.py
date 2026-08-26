from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Revenue Recovery Platform",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
    )
    app.include_router(health_router)
    return app


app = create_app()
