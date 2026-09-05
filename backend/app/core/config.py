from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from app.core.auth import Role


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

    # --- Async event architecture (Phase 12, ADR-007) ---
    # When unset, the relay/consumer scripts cannot connect and log that
    # fact rather than silently doing nothing -- the outbox pattern means
    # domain writes (app/events/publisher.py::OutboxEventPublisher) never
    # depend on Kafka being reachable at all; only the relay does.
    kafka_bootstrap_servers: str | None = None
    kafka_domain_events_topic: str = "arr.domain-events"
    kafka_consumer_group: str = "arr-event-audit-projector"
    # Bounded retry before a consumed event is routed to the dead-letter
    # table (ADR-007) -- not an infinite retry loop, not a silent drop.
    event_consumer_max_attempts: int = 5

    # --- Security & fintech hardening (Phase 15) ---
    # "key1:operator,key2:readonly" -- see app.core.auth.parse_api_keys.
    # Empty by default; production refuses to start with no keys
    # configured (app.main.create_app), development/test do not (every
    # request is simply unauthenticated-401 until keys exist).
    api_keys_raw: str = ""
    # Comma-separated list of allowed browser origins for CORS. Empty by
    # default (no cross-origin browser access permitted) -- the bundled
    # frontend calls the backend server-to-server (API_BASE_URL, not
    # NEXT_PUBLIC_*, per KI-001), so it does not need a CORS allowance at
    # all; this exists for any future browser client calling the API
    # directly from a different origin.
    cors_allowed_origins_raw: str = ""
    # Sustained request budget per API key (or per client IP for
    # unauthenticated requests, which today means every request that
    # will shortly be rejected 401 by app.core.auth) in a fixed window.
    rate_limit_requests_per_minute: int = 120

    # --- Real payment integration (Phase 16) ---
    # A Stripe TEST-mode secret key (sk_test_...) -- see
    # app.decision.providers_stripe. Unset by default: the retry channel
    # falls back to the existing SimulatedPaymentProvider, the same
    # "config-gated, fallback rather than fail" shape the Phase 4
    # reasoning-model factory already uses. A live key (sk_live_...) is
    # refused at the point of use (StripeConfigurationError), never
    # silently accepted -- this platform does not process real payments.
    stripe_api_key: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def api_keys(self) -> dict[str, "Role"]:
        from app.core.auth import parse_api_keys

        return parse_api_keys(self.api_keys_raw)

    @property
    def cors_allowed_origins(self) -> list[str]:
        origins = self.cors_allowed_origins_raw.split(",")
        return [origin.strip() for origin in origins if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
