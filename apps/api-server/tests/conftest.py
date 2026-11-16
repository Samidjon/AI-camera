"""Test fixtures. Integration tests run the real app against a migrated SQLite database,
so they need no network, no Supabase and no Redis. PostgreSQL-specific migration tests
run only when TEST_DATABASE_URL is set (use a disposable database — it is wiped)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import build_engine, set_engine
from app.main import create_app

API_ROOT = Path(__file__).resolve().parents[1]

# Make sure a developer's real .env (Supabase credentials) is never used by tests.
os.environ.setdefault("ACI_ENVIRONMENT", "test")


def run_migrations(url: str, revision: str = "head", *, downgrade: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    if downgrade:
        command.downgrade(cfg, revision)
    else:
        command.upgrade(cfg, revision)


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def settings(sqlite_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=sqlite_url,
        redis_url="",
        jwt_secret="test-secret-that-is-long-enough-for-hs256-0123456789",
        log_json=False,
        log_level="WARNING",
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings.database_url)
    eng = build_engine(settings.database_url)
    set_engine(eng)
    yield eng
    set_engine(None)


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
