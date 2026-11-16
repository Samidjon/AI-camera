"""Local SQLite database of the edge agent (stdlib sqlite3 — no server, works offline).

Schema versions are tracked with ``PRAGMA user_version``; migrations are applied in order
inside a transaction. WAL mode keeps readers (API) and writers (status persistence) from
blocking each other and survives power loss better than the rollback journal.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: list[str] = [
    # v1 — MVP 02: cameras, groups, encrypted secrets
    """
    CREATE TABLE camera_groups (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE cameras (
        id                TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        location          TEXT,
        source_type       TEXT NOT NULL CHECK (source_type IN ('rtsp','onvif','usb','file','ffmpeg')),
        source_uri        TEXT NOT NULL,
        options           TEXT NOT NULL DEFAULT '{}',
        group_id          TEXT REFERENCES camera_groups(id) ON DELETE SET NULL,
        is_enabled        INTEGER NOT NULL DEFAULT 1,
        resolution_width  INTEGER,
        resolution_height INTEGER,
        fps               REAL,
        last_status       TEXT,
        last_error        TEXT,
        last_connected_at TEXT,
        last_frame_at     TEXT,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    );
    CREATE INDEX ix_cameras_group_id ON cameras(group_id);
    -- Secrets live in their own table, encrypted (Fernet), never joined into API responses.
    -- NOTE: statements are split on semicolons, so comments must not contain one.
    CREATE TABLE camera_secrets (
        camera_id  TEXT PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
        ciphertext BLOB NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # v2 — MVP 03: per-camera vision settings, zones and crossing lines (normalized 0..1 coordinates)
    """
    CREATE TABLE vision_configs (
        camera_id  TEXT PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
        config     TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE zones (
        id         TEXT PRIMARY KEY,
        camera_id  TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
        name       TEXT NOT NULL,
        role       TEXT NOT NULL CHECK (role IN ('monitor','exclude')),
        polygon    TEXT NOT NULL,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (camera_id, name)
    );
    CREATE INDEX ix_zones_camera_id ON zones(camera_id);
    CREATE TABLE crossing_lines (
        id         TEXT PRIMARY KEY,
        camera_id  TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
        name       TEXT NOT NULL,
        ax REAL NOT NULL, ay REAL NOT NULL, bx REAL NOT NULL, by REAL NOT NULL,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (camera_id, name)
    );
    CREATE INDEX ix_crossing_lines_camera_id ON crossing_lines(camera_id)
    """,
]

# Separate database for events: high write volume, own retention, can be quarantined and rebuilt
# without touching camera configuration.
EVENT_MIGRATIONS: list[str] = [
    """
    CREATE TABLE events (
        id          TEXT PRIMARY KEY,
        camera_id   TEXT NOT NULL,
        event_type  TEXT NOT NULL,
        source      TEXT NOT NULL DEFAULT 'standard',
        occurred_at TEXT NOT NULL,
        zone_id     TEXT,
        line_id     TEXT,
        track_id    TEXT,
        metadata    TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL
    );
    CREATE INDEX ix_events_camera_time ON events(camera_id, occurred_at);
    CREATE INDEX ix_events_type_time ON events(event_type, occurred_at);
    CREATE INDEX ix_events_time ON events(occurred_at)
    """,
]


class Database:
    def __init__(self, path: Path | str, migrations: list[str] | None = None) -> None:
        self.path = str(path)
        self._migrations = MIGRATIONS if migrations is None else migrations
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
            self.migrate()
        except Exception:
            # Release the file handle: on Windows an open handle would block quarantining a corrupt file.
            self._conn.close()
            raise

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        with self._lock:
            current = self.schema_version
            for version, script in enumerate(self._migrations, start=1):
                if version <= current:
                    continue
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in filter(str.strip, script.split(";")):
                        self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {version}")
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def integrity_ok(self) -> bool:
        with self._lock:
            return self._conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        """Autocommit statement; returns affected rows."""
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def checkpoint(self) -> None:
        """Flush the WAL into the main file (safe shutdown / before backups)."""
        if self.path != ":memory:":
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
