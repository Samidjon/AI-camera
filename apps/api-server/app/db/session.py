"""Engine / session management. The engine is created lazily so the API can start
(and report a clear `not_configured` status) even when no database is configured."""

from __future__ import annotations

from collections.abc import Iterator
from threading import Lock
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = Lock()


def build_engine(url: str, *, pool_size: int = 5, echo: bool = False) -> Engine:
    kwargs: dict = {"echo": echo, "pool_pre_ping": True}
    connect_args: dict = {}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=pool_size, max_overflow=pool_size, pool_recycle=1800)
        # Supabase transaction pooler (port 6543 / PgBouncer) does not support prepared statements.
        connect_args["prepare_threshold"] = None
        connect_args["connect_timeout"] = 5
    elif url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, **kwargs)


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    settings = settings or get_settings()
    if not settings.database_configured:
        raise ServiceUnavailableError(
            "Database is not configured (set ACI_DATABASE_URL)", code="database_not_configured"
        )
    with _lock:
        if _engine is None:
            _engine = build_engine(
                settings.database_url, pool_size=settings.database_pool_size, echo=settings.database_echo
            )
            _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def set_engine(engine: Engine | None) -> None:
    """Override the engine (used by tests and app shutdown)."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None and _engine is not engine:
            _engine.dispose()
        _engine = engine
        _session_factory = (
            sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None
        )


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    if not settings.database_configured:
        raise ServiceUnavailableError(
            "Database is not configured (set ACI_DATABASE_URL)", code="database_not_configured"
        )
    get_engine(settings)
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_database(settings: Settings | None = None) -> None:
    with get_engine(settings).connect() as conn:
        conn.execute(text("SELECT 1"))
