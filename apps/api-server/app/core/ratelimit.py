"""In-process token-bucket rate limiter.

Keys are device IDs or client IPs. State lives in this process: with several API workers each
worker limits independently (a shared Redis limiter is planned with horizontal scaling, MVP 09).
"""

from __future__ import annotations

import math
import threading
import time

from app.core.errors import RateLimitedError


class RateLimiter:
    def __init__(self, max_keys: int = 50_000, clock=time.monotonic) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._clock = clock
        self.rejected = 0

    def check(self, key: str, rate_per_s: float, burst: int) -> float:
        """Take one token. Returns 0 when allowed, otherwise seconds until a token is available."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(burst), now))
            tokens = min(float(burst), tokens + (now - last) * rate_per_s)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                wait = 0.0
            else:
                self._buckets[key] = (tokens, now)
                wait = (1 - tokens) / rate_per_s
                self.rejected += 1
            if len(self._buckets) > self._max_keys:
                # Drop the idlest half: full buckets carry no state worth keeping.
                for k, _ in sorted(self._buckets.items(), key=lambda kv: kv[1][1])[: self._max_keys // 2]:
                    del self._buckets[k]
        return wait

    def enforce(self, key: str, rate_per_s: float, burst: int) -> None:
        wait = self.check(key, rate_per_s, burst)
        if wait > 0:
            retry = max(1, math.ceil(wait))
            raise RateLimitedError(
                "Too many requests — slow down", details={"retry_after_s": retry}, headers={"Retry-After": str(retry)}
            )


limiter = RateLimiter()
