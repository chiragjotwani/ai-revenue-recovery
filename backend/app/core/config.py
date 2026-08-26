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

    database_url: str = "postgresql+psycopg://arr_user:arr_password@localhost:5432/arr_db"
    redis_url: str = "redis://localhost:6379/0"

    backend_port: int = 8000

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
