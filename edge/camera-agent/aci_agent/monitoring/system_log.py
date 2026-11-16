"""System log stored in the local database (``system_logs``), viewable from the dashboard.

* Non-blocking: records go into a bounded in-memory queue; a writer thread inserts them in batches.
  When the queue is full the record is dropped and counted (logging must never stall capture).
* Bounded on disk: only the newest ``max_rows`` rows are kept.
* Messages pass through the same redaction as console logs — no credentials in the database.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import UTC, datetime

from aci_agent.monitoring.logging import redact
from aci_agent.storage.database import Database

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class SystemLogHandler(logging.Handler):
    def __init__(self, db: Database, level: int | str = logging.INFO, max_rows: int = 5000) -> None:
        super().__init__(level)
        self._db = db
        self.max_rows = max_rows
        self._queue: queue.Queue[tuple[str, str, str, str] | None] = queue.Queue(maxsize=2000)
        self.dropped = 0
        self._written_since_trim = 0
        self._thread = threading.Thread(target=self._writer, name="system-log-writer", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("aci.agent.systemlog"):
            return
        try:
            message = redact(record.getMessage())[:1000]
            if record.exc_info and record.exc_info[0] is not None:
                message += f" [{record.exc_info[0].__name__}]"
            row = (
                datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                record.levelname,
                record.name.removeprefix("aci.agent.")[:60],
                message,
            )
            self._queue.put_nowait(row)
        except queue.Full:
            self.dropped += 1
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def _writer(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            batch = [item]
            while len(batch) < 200:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    self._flush(batch)
                    return
                batch.append(nxt)
            self._flush(batch)

    def _flush(self, batch: list) -> None:
        try:
            with self._db.transaction() as conn:
                conn.executemany("INSERT INTO system_logs (at, level, component, message) VALUES (?, ?, ?, ?)", batch)
                self._written_since_trim += len(batch)
                if self._written_since_trim >= 100:
                    self._written_since_trim = 0
                    conn.execute(
                        "DELETE FROM system_logs WHERE id <= (SELECT MAX(id) FROM system_logs) - ?", (self.max_rows,)
                    )
        except Exception:  # noqa: BLE001 - a closed/locked database must not crash the logger
            self.dropped += len(batch)

    def close(self) -> None:
        try:
            self._queue.put(None, timeout=1)
            self._thread.join(2)
        except queue.Full:
            pass
        super().close()


def install(db: Database, level: str = "INFO", max_rows: int = 5000) -> SystemLogHandler:
    handler = SystemLogHandler(db, level, max_rows)
    logging.getLogger("aci").addHandler(handler)
    return handler


def uninstall(handler: SystemLogHandler) -> None:
    logging.getLogger("aci").removeHandler(handler)
    handler.close()


def read_logs(db: Database, level: str | None = None, limit: int = 200, component: str | None = None) -> list[dict]:
    clauses, params = [], []
    if level:
        level = level.upper()
        if level not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        wanted = LEVELS[LEVELS.index(level) :]
        clauses.append(f"level IN ({','.join('?' * len(wanted))})")
        params.extend(wanted)
    if component:
        clauses.append("component LIKE ?")
        params.append(f"{component[:60]}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 1000)))
    rows = db.query(f"SELECT id, at, level, component, message FROM system_logs {where} ORDER BY id DESC LIMIT ?",
                    tuple(params))  # fmt: skip
    return [dict(r) for r in rows]
