"""Exponential backoff with full jitter (AWS-style): delay = random(0, min(max, initial * 2^n))."""

from __future__ import annotations

import random


class Backoff:
    def __init__(self, initial_s: float, max_s: float, rng: random.Random | None = None) -> None:
        self.initial_s = initial_s
        self.max_s = max_s
        self.failures = 0
        self._rng = rng or random.Random()

    def next_delay(self, minimum: float | None = None) -> float:
        cap = min(self.max_s, self.initial_s * (2**self.failures))
        self.failures = min(self.failures + 1, 32)
        # Never retry sooner than ``initial_s / 2`` (no hot loop), honour a server's Retry-After.
        delay = max(self.initial_s / 2, self._rng.uniform(0, cap))
        if minimum is not None:
            delay = max(delay, min(minimum, self.max_s))
        return delay

    def reset(self) -> None:
        self.failures = 0
