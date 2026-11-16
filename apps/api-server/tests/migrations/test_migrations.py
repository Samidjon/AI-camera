"""Database migration tests.

* SQLite: upgrade → downgrade → upgrade, plus model/migration drift check. Always runs.
* PostgreSQL: offline SQL generation always runs (no DB needed); an online
  upgrade/downgrade runs only when TEST_DATABASE_URL is set. Point it at a DISPOSABLE
  database (e.g. a separate Supabase project / branch) — tables are dropped.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import build_engine
from tests.conftest import API_ROOT, run_migrations

EXPECTED_TABLES = {
    "users",
    "roles",
    "cameras",
    "camera_groups",
    "camera_events",
    "system_logs",
    "audit_logs",
    "device_groups",
    "edge_devices",
    "device_credentials",
    "device_enrollment_tokens",
    "device_heartbeats",
    "synchronization_logs",
    "device_configurations",
    "event_statistics",
    "system_settings",
    "alert_rules",
    "alerts",
    "notification_deliveries",
}


def _tables(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def test_sqlite_upgrade_downgrade_cycle(sqlite_url: str):
    run_migrations(sqlite_url)
    assert _tables(sqlite_url) == EXPECTED_TABLES

    eng = create_engine(sqlite_url)
    with eng.connect() as conn:
        roles = set(conn.execute(text("SELECT name FROM roles")).scalars())
    eng.dispose()
    assert roles == {"administrator", "operator", "viewer"}

    run_migrations(sqlite_url, "base", downgrade=True)
    assert _tables(sqlite_url) == set()

    run_migrations(sqlite_url)
    assert _tables(sqlite_url) == EXPECTED_TABLES


def test_models_match_migrations(sqlite_url: str):
    """Fails if someone changes a model without writing a migration."""
    run_migrations(sqlite_url)
    eng = create_engine(sqlite_url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    eng.dispose()
    assert diff == [], f"Models and migrations differ: {diff}"


def test_check_constraints_enforced(sqlite_url: str):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from app.models import Camera

    run_migrations(sqlite_url)
    eng = build_engine(sqlite_url)
    with Session(eng) as s:
        s.add(Camera(name="bad", source_type="vhs"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        s.add(Camera(name="ok", source_type="rtsp"))
        s.commit()
        cam = s.scalar(select(Camera))
        assert cam.status == "offline"
    eng.dispose()


def test_postgres_offline_sql_generation():
    """Renders the full PostgreSQL DDL without a database connection."""
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.attributes["database_url"] = "postgresql+psycopg://u:p@localhost/db"
    cfg.attributes["configure_logger"] = False
    buf = io.StringIO()
    cfg.output_buffer = buf
    with redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE {table}" in sql
    assert "JSONB" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "ON DELETE SET NULL" in sql


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_upgrade_downgrade_cycle():
    url = os.environ["TEST_DATABASE_URL"]
    run_migrations(url, "base", downgrade=True)
    run_migrations(url)
    from app.core.config import normalize_database_url

    eng = build_engine(normalize_database_url(url))
    try:
        assert EXPECTED_TABLES <= set(inspect(eng).get_table_names())
        with eng.connect() as conn:
            rls = (
                conn.execute(
                    text("SELECT relname FROM pg_class WHERE relrowsecurity AND relname = ANY(:t)"),
                    {"t": list(EXPECTED_TABLES)},
                )
                .scalars()
                .all()
            )
        assert set(rls) == EXPECTED_TABLES
    finally:
        eng.dispose()
    run_migrations(url, "base", downgrade=True)
    run_migrations(url)
