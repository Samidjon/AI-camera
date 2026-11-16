"""Frame-flow monitor: measures real FPS and detects stalled streams.

A camera is "online" because frames arrive, not because a socket opened. A stream that
stops delivering frames for ``stale_after_s`` is reported as stalled even while the
reader thread is still blocked inside the decoder.
"""

from __future__ import annotations

import threading
import time


class ConnectionMonitor:
    def __init__(self, stale_after_s: float, smoothing: float = 0.2) -> None:
        self.stale_after_s = stale_after_s
        self._alpha = smoothing
        self._lock = threading.Lock()
        self._last_frame_mono: float | None = None
        self._interval_ema: float | None = None

    def reset(self) -> None:
        with self._lock:
            self._last_frame_mono = None
            self._interval_ema = None

    def on_frame(self, now_mono: float | None = None) -> None:
        now = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            if self._last_frame_mono is not None:
                interval = max(now - self._last_frame_mono, 1e-4)
                self._interval_ema = (
                    interval
                    if self._interval_ema is None
                    else self._alpha * interval + (1 - self._alpha) * self._interval_ema
                )
            self._last_frame_mono = now

    @property
    def fps(self) -> float | None:
        with self._lock:
            return round(1.0 / self._interval_ema, 1) if self._interval_ema else None

    def seconds_since_frame(self, now_mono: float | None = None) -> float | None:
        now = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            return None if self._last_frame_mono is None else now - self._last_frame_mono

    def is_stale(self, now_mono: float | None = None) -> bool:
        age = self.seconds_since_frame(now_mono)
        return age is None or age > self.stale_after_s
