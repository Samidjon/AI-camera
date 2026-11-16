"""Process resource monitoring (CPU, memory) used to protect the device.

Only metrics that the platform actually provides are reported; nothing is estimated.
"""

from __future__ import annotations

import threading
import time

import psutil


class ResourceMonitor:
    def __init__(self, min_interval_s: float = 1.0) -> None:
        self._proc = psutil.Process()
        self._proc.cpu_percent(None)  # prime the counter
        psutil.cpu_percent(None)
        self._lock = threading.Lock()
        self._min_interval = min_interval_s
        self._last: dict | None = None
        self._last_at = 0.0

    def sample(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._last is None or now - self._last_at >= self._min_interval:
                mem = self._proc.memory_info()
                self._last = {
                    "process_cpu_percent": round(self._proc.cpu_percent(None), 1),
                    "system_cpu_percent": round(psutil.cpu_percent(None), 1),
                    "cpu_count": psutil.cpu_count() or 1,
                    "process_rss_mb": round(mem.rss / 1_048_576, 1),
                    "system_memory_percent": round(psutil.virtual_memory().percent, 1),
                    "threads": self._proc.num_threads(),
                }
                self._last_at = now
            return dict(self._last)

    def rss_mb(self) -> float:
        return self._proc.memory_info().rss / 1_048_576
