"""Dependency health checks. Each check is isolated: one failing component never
breaks the status endpoint or the other checks."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app.core.config import Settings
from app.core.logging import redact
from app.db.session import get_engine, ping_database
from app.schemas.system import ComponentHealth, ComponentState

logger = logging.getLogger("aci.health")


def _timed(name: str, fn: Callable[[], None]) -> ComponentHealth:
    started = time.perf_counter()
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — health checks must never raise
        logger.warning("Health check failed for %s: %s", name, redact(str(exc)))
        return ComponentHealth(
            state=ComponentState.ERROR,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=redact(type(exc).__name__ + ": " + str(exc))[:300],
        )
    return ComponentHealth(
        state=ComponentState.OK, latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


def check_database(settings: Settings) -> ComponentHealth:
    if not settings.database_configured:
        return ComponentHealth(state=ComponentState.NOT_CONFIGURED, detail="ACI_DATABASE_URL is empty")
    result = _timed("database", lambda: ping_database(settings))
    if result.state == ComponentState.OK:
        # Report the real backend so a SQLite dev database is never displayed as PostgreSQL.
        result.detail = get_engine(settings).dialect.name
    return result


def check_redis(settings: Settings) -> ComponentHealth:
    if not settings.redis_configured:
        return ComponentHealth(
            state=ComponentState.NOT_CONFIGURED,
            detail="Redis is optional; set ACI_REDIS_URL to enable it",
        )

    def _ping() -> None:
        import redis  # local import: optional component

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
        finally:
            client.close()

    return _timed("redis", _ping)
