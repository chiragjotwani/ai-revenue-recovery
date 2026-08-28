from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables.

    This is the single point where environment-derived configuration
    enters the application. No other module should read os.environ
    directly.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://arr_user:arr_password@localhost:5433/arr_db"
    redis_url: str = "redis://localhost:6379/0"

    backend_port: int = 8000

    # --- Reasoning model (Phase 4) ---
    # Which ReasoningModel implementation to use for diagnosis:
    # "mock" (default; deterministic, no model needed), "qwen", or "nemotron".
    reasoning_provider: str = "mock"
    # OpenAI-compatible base URLs for the self-hosted providers, e.g.
    # "http://localhost:11434/v1" for a local Ollama server. When the URL
    # for the selected provider is unset, the app falls back to the mock
    # provider rather than failing. See docs/ai/local-model-setup.md.
    ai_qwen_base_url: str | None = None
    ai_qwen_model: str = "qwen3:4b"
    ai_nemotron_base_url: str | None = None
    ai_nemotron_model: str = "nemotron-mini"
    ai_request_timeout_seconds: float = 60.0

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
