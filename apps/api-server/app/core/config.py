"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "change-me-in-production-please-use-a-long-random-value"


def normalize_database_url(value: str) -> str:
    """Supabase dashboards hand out postgresql:// / postgres:// URLs; use the psycopg3 driver."""
    value = value.strip()
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+psycopg://" + value[len(prefix) :]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ACI_",
        extra="ignore",
    )

    app_name: str = "AI Camera Intelligence API"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Database. Central server uses PostgreSQL (hosted on Supabase).
    # Example: postgresql+psycopg://postgres.<ref>:<password>@<host>:5432/postgres?sslmode=require
    database_url: str = ""
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_echo: bool = False

    # Redis is optional for the central server and never required by the edge agent.
    redis_url: str = ""

    # Auth
    jwt_secret: str = INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1, le=60 * 24)

    # HTTP
    cors_origins: list[str] = ["http://localhost:3000"]

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _check_production_secret(self) -> Settings:
        if self.environment == "production":
            if self.jwt_secret == INSECURE_DEFAULT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("ACI_JWT_SECRET must be set to a random value of 32+ chars in production")
            if not self.database_url:
                raise ValueError("ACI_DATABASE_URL is required in production")
        return self

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def redis_configured(self) -> bool:
        return bool(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
