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
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        with self._lock:
            current = self.schema_version
            for version, script in enumerate(MIGRATIONS, start=1):
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
