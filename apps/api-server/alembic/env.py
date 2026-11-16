from __future__ import annotations

from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401 — register all tables on Base.metadata
from app.core.config import get_settings, normalize_database_url
from app.db.base import Base
from app.db.session import build_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # Tests (or tooling) may pass an explicit URL; otherwise use app settings.
    url = config.attributes.get("database_url") or config.get_main_option("sqlalchemy.url")
    if not url:
        url = get_settings().database_url
    if not url:
        raise RuntimeError("ACI_DATABASE_URL is not set — cannot run migrations")
    return normalize_database_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = build_engine(_database_url(), pool_size=1)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
