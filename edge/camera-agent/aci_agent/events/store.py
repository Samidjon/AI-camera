"""Local event database (SQLite) with a non-blocking writer, retention and self-healing.

* ``append()`` never blocks the vision threads: events go into a bounded queue; a single writer
  thread inserts them in batches. If the queue is full the event is dropped and counted.
* Retention: events older than ``retention_days`` are deleted; if the database grows beyond
  ``max_db_mb`` or free disk space falls below ``min_free_disk_mb`` the oldest events are pruned.
  If disk space stays critical, writing pauses (events are counted as dropped) — capture and
  analysis keep running.
* Integrity: ``PRAGMA quick_check`` on open (and on demand). A corrupt or unreadable file is moved
  aside (``events.db.corrupt-<timestamp>``) and a fresh database is created, so the agent keeps
  working; the quarantined file is kept for manual recovery.
* Safe shutdown: ``close()`` drains the queue, checkpoints the WAL and closes the connection.
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aci_agent.events.models import Event
from aci_agent.storage.database import EVENT_MIGRATIONS, Database

logger = logging.getLogger("aci.agent.events")


@dataclass(slots=True)
class EventStoreSettings:
    retention_days: int = 30
    max_db_mb: int = 512
    min_free_disk_mb: int = 500
    queue_size: int = 10_000
    batch_size: int = 500
    flush_interval_s: float = 0.5
    maintenance_interval_s: float = 300.0
    initial_maintenance_delay_s: float = 10.0  # first cleanup shortly after start (boot-time retention)


class EventStore:
    def __init__(
        self,
        path: Path,
        settings: EventStoreSettings | None = None,
        disk_usage: Callable[[str], tuple[int, int, int]] = shutil.disk_usage,
    ) -> None:
        self.path = Path(path)
        self.settings = settings or EventStoreSettings()
        self._disk_usage = disk_usage
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=self.settings.queue_size)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.dropped = 0
        self.written = 0
        self._pending = 0  # accepted but not yet written (or dropped)
        self._pending_cond = threading.Condition()
        self.recoveries: list[str] = []
        self.storage_paused = False
        self.last_maintenance: datetime | None = None
        self.db = self._open()
        self._thread = threading.Thread(target=self._run, name="event-writer", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ open / recovery

    def _open(self) -> Database:
        try:
            db = Database(self.path, EVENT_MIGRATIONS)
            if not db.integrity_ok():
                db.close()
                raise sqlite3.DatabaseError("integrity check failed")
            return db
        except sqlite3.DatabaseError as exc:
            return self._quarantine_and_recreate(str(exc))

    def _quarantine_and_recreate(self, reason: str) -> Database:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        moved = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                src.replace(Path(str(moved) + suffix))
        logger.error("Event database unusable (%s); moved to %s and recreated", reason, moved.name)
        self.recoveries.append(f"{stamp}: {reason}")
        return Database(self.path, EVENT_MIGRATIONS)

    # ------------------------------------------------------------------ writing

    def append(self, event: Event) -> bool:
        if self._stop.is_set() or self.storage_paused:
            self.dropped += 1
            return False
        try:
            with self._pending_cond:
                self._queue.put_nowait(event)
                self._pending += 1
            return True
        except queue.Full:
            self.dropped += 1
            if self.dropped % 1000 == 1:
                logger.warning("Event queue full — dropping events (total dropped: %d)", self.dropped)
            return False

    def _drain(self, max_items: int, timeout: float) -> list[Event]:
        batch: list[Event] = []
        try:
            batch.append(self._queue.get(timeout=timeout))
        except queue.Empty:
            return batch
        while len(batch) < max_items:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write(self, batch: list[Event]) -> None:
        rows = [
            (
                e.id, e.camera_id, e.event_type, e.source, e.occurred_at.isoformat(), e.zone_id, e.line_id,
                e.track_id, json.dumps(e.metadata, default=str), datetime.now(UTC).isoformat(),
            )
            for e in batch
        ]  # fmt: skip
        sql = (
            "INSERT OR IGNORE INTO events (id, camera_id, event_type, source, occurred_at, zone_id, line_id, "
            "track_id, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        for attempt in (1, 2):
            try:
                with self._lock, self.db.transaction() as conn:
                    conn.executemany(sql, rows)
                self.written += len(rows)
                return
            except sqlite3.DatabaseError as exc:
                if "malformed" in str(exc) or "not a database" in str(exc):
                    with self._lock:
                        try:
                            self.db.close()
                        except Exception:  # noqa: BLE001
                            pass
                        self.db = self._quarantine_and_recreate(str(exc))
                elif attempt == 2:
                    self.dropped += len(rows)
                    logger.error("Failed to store %d events: %s", len(rows), exc)
                    return
                time.sleep(0.1)

    def _run(self) -> None:
        next_maintenance = time.monotonic() + self.settings.initial_maintenance_delay_s
        while not self._stop.is_set() or not self._queue.empty():
            batch = self._drain(self.settings.batch_size, self.settings.flush_interval_s)
            if batch:
                try:
                    self._write(batch)
                finally:
                    with self._pending_cond:
                        self._pending -= len(batch)
                        self._pending_cond.notify_all()
            if time.monotonic() >= next_maintenance and not self._stop.is_set():
                next_maintenance = time.monotonic() + self.settings.maintenance_interval_s
                try:
                    self.maintenance()
                except Exception:  # noqa: BLE001 — maintenance must never kill the writer
                    logger.exception("Event maintenance failed")

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every accepted event has been written. Returns False on timeout."""
        with self._pending_cond:
            return self._pending_cond.wait_for(lambda: self._pending == 0, timeout)

    def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._thread.join(timeout)
        with self._lock:
            try:
                self.db.checkpoint()
            finally:
                self.db.close()

    # ------------------------------------------------------------------ retention & disk

    def db_size_bytes(self) -> int:
        return sum(Path(str(self.path) + s).stat().st_size for s in ("", "-wal") if Path(str(self.path) + s).exists())

    def used_bytes(self) -> int:
        """Bytes occupied by live data. SQLite keeps freed pages in the file after DELETE, so the
        file size alone would make size-based pruning repeat forever."""
        page_size = int(self.db.query("PRAGMA page_size")[0][0])
        pages = int(self.db.query("PRAGMA page_count")[0][0]) - int(self.db.query("PRAGMA freelist_count")[0][0])
        return pages * page_size

    def free_disk_mb(self) -> float:
        return self._disk_usage(str(self.path.parent))[2] / 1_048_576

    def maintenance(self) -> dict:
        s = self.settings
        report = {"expired": 0, "pruned_size": 0, "pruned_disk": 0}
        cutoff = (datetime.now(UTC) - timedelta(days=s.retention_days)).isoformat()
        with self._lock:
            report["expired"] = self.db.execute("DELETE FROM events WHERE occurred_at < ?", (cutoff,))
            if self.used_bytes() > s.max_db_mb * 1_048_576:
                report["pruned_size"] = self._prune_oldest(0.10)
            free = self.free_disk_mb()
            if free < s.min_free_disk_mb:
                report["pruned_disk"] = self._prune_oldest(0.20)
                free = self.free_disk_mb()
            # Pause writing only when disk is critically low (half the minimum); resume above the minimum.
            if free < s.min_free_disk_mb / 2 and not self.storage_paused:
                self.storage_paused = True
                logger.error("Free disk space critically low (%.0f MB) — event storage paused", free)
            elif free >= s.min_free_disk_mb and self.storage_paused:
                self.storage_paused = False
                logger.warning("Disk space recovered — event storage resumed")
        self.last_maintenance = datetime.now(UTC)
        if any(report.values()):
            logger.info("Event retention", extra=report)
        return report

    def _prune_oldest(self, fraction: float) -> int:
        total = int(self.db.query("SELECT COUNT(*) FROM events")[0][0])
        n = max(1, int(total * fraction)) if total else 0
        if not n:
            return 0
        return self.db.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY occurred_at LIMIT ?)", (n,)
        )

    def integrity_check(self) -> bool:
        with self._lock:
            return self.db.integrity_ok()

    # ------------------------------------------------------------------ queries

    def query(
        self,
        camera_id: str | None = None,
        event_types: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[str, str] | None = None,  # keyset cursor (occurred_at, id)
        limit: int = 100,
    ) -> list[dict]:
        where, params = [], []
        if camera_id:
            where.append("camera_id = ?")
            params.append(camera_id)
        if event_types:
            where.append(f"event_type IN ({','.join('?' * len(event_types))})")
            params.extend(event_types)
        if since:
            where.append("occurred_at >= ?")
            params.append(since.astimezone(UTC).isoformat())
        if until:
            where.append("occurred_at < ?")
            params.append(until.astimezone(UTC).isoformat())
        if before:
            where.append("(occurred_at < ? OR (occurred_at = ? AND id < ?))")
            params.extend([before[0], before[0], before[1]])
        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self.db.query(sql, tuple(params))
        return [dict(r) | {"metadata": json.loads(r["metadata"] or "{}")} for r in rows]

    def line_counts(self, camera_id: str, since: datetime) -> dict[str, dict[str, int]]:
        with self._lock:
            rows = self.db.query(
                "SELECT line_id, json_extract(metadata, '$.direction') AS direction, COUNT(*) AS n FROM events "
                "WHERE camera_id = ? AND event_type = 'line_crossed' AND occurred_at >= ? GROUP BY line_id, direction",
                (camera_id, since.astimezone(UTC).isoformat()),
            )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["line_id"], {"forward": 0, "backward": 0})[r["direction"] or "forward"] = r["n"]
        return out

    def counts_by_type(self, since: datetime, camera_id: str | None = None) -> dict[str, int]:
        sql = "SELECT event_type, COUNT(*) AS n FROM events WHERE occurred_at >= ?"
        params: list = [since.astimezone(UTC).isoformat()]
        if camera_id:
            sql += " AND camera_id = ?"
            params.append(camera_id)
        with self._lock:
            rows = self.db.query(sql + " GROUP BY event_type", tuple(params))
        return {r["event_type"]: r["n"] for r in rows}

    def stats(self) -> dict:
        with self._lock:
            row = self.db.query(
                "SELECT COUNT(*) AS n, MIN(occurred_at) AS oldest, MAX(occurred_at) AS newest FROM events"
            )[0]
        return {
            "events_total": row["n"],
            "oldest": row["oldest"],
            "newest": row["newest"],
            "db_size_mb": round(self.db_size_bytes() / 1_048_576, 2),
            "db_used_mb": round(self.used_bytes() / 1_048_576, 2),
            "free_disk_mb": round(self.free_disk_mb(), 1),
            "queue_length": self._queue.qsize(),
            "written": self.written,
            "dropped": self.dropped,
            "storage_paused": self.storage_paused,
            "recoveries": list(self.recoveries),
            "retention_days": self.settings.retention_days,
            "last_maintenance": self.last_maintenance.isoformat() if self.last_maintenance else None,
        }
