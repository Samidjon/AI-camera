"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote, unquote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "change-me-in-production-please-use-a-long-random-value"


# Query parameters some tools add to Supabase URLs (Prisma: pgbouncer, connection_limit, ...)
# that libpq/psycopg reject with "invalid connection option".
_NON_LIBPQ_PARAMS = {"pgbouncer", "connection_limit", "pool_timeout", "statement_cache_size", "schema"}


def normalize_database_url(value: str) -> str:
    """Supabase dashboards hand out postgresql:// / postgres:// URLs; use the psycopg3 driver
    and drop ORM-specific query parameters that the PostgreSQL driver does not understand."""
    value = value.strip()
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            value = "postgresql+psycopg://" + value[len(prefix) :]
            break
    if not value.startswith("postgresql") or "://" not in value:
        return value

    scheme, _, rest = value.partition("://")
    # The host part starts after the LAST "@", so passwords may contain "@", "/", "?", "#"...
    userinfo, at, hostpart = rest.rpartition("@")
    if at:
        user, colon, password = userinfo.partition(":")
        if colon:
            # Percent-encode the password exactly once (people paste raw passwords from Supabase).
            userinfo = f"{user}:{quote(unquote(password), safe='')}"
    hostpart, qmark, query = hostpart.partition("?")
    kept = [p for p in query.split("&") if p and p.split("=", 1)[0].lower() not in _NON_LIBPQ_PARAMS]
    url = f"{scheme}://{userinfo}{at}{hostpart}"
    return f"{url}?{'&'.join(kept)}" if kept else url


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
