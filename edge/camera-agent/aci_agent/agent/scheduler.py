"""Periodic local housekeeping (automatic database backups)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("aci.agent.scheduler")


class BackupScheduler:
    def __init__(self, interval_h: float, run_backup: Callable[[], dict], first_delay_s: float = 600.0) -> None:
        self.interval_s = interval_h * 3600
        self._run_backup = run_backup
        self._first_delay = first_delay_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_result: dict | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self.interval_s <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="backup-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        delay = self._first_delay
        while not self._stop.wait(delay):
            try:
                self.last_result = self._run_backup()
                self.last_error = None
                logger.info("Automatic backup created", extra={"backup": self.last_result.get("id")})
            except Exception as exc:  # noqa: BLE001 - a failed backup must not affect the agent
                self.last_error = str(exc)[:200]
                logger.error("Automatic backup failed: %s", self.last_error)
            delay = self.interval_s

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
