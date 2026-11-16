"""Bounded frame containers. Nothing here can grow without limit.

* ``LatestFrameBuffer`` keeps exactly one frame (the newest). Used by live preview/snapshots.
* ``FrameQueue`` is a bounded FIFO for analysis consumers (Standard Vision Engine, MVP 03).
  When full it drops the OLDEST frame so consumers always see recent video and the
  capture thread never blocks on a slow consumer.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, slots=True)
class Frame:
    seq: int
    timestamp: float  # time.time() when received
    image: np.ndarray = field(repr=False)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


class LatestFrameBuffer:
    """Single-slot buffer with change notification and a per-frame encode cache."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        # JPEG encodings of the current frame, one per preview variant (size / overlay layers).
        self._encoded_seq = -1
        self._encoded: dict = {}
        self._closed = False

    def publish(self, frame: Frame) -> None:
        with self._cond:
            self._frame = frame
            self._cond.notify_all()

    def latest(self) -> Frame | None:
        with self._cond:
            return self._frame

    def wait_newer(self, after_seq: int, timeout: float) -> Frame | None:
        """Block until a frame with seq > after_seq exists (or timeout / close)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._closed and (self._frame is None or self._frame.seq <= after_seq):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._frame if self._frame and self._frame.seq > after_seq else None

    def cached_jpeg(self, seq: int, variant) -> bytes | None:
        with self._cond:
            return self._encoded.get(variant) if self._encoded_seq == seq else None

    def store_jpeg(self, seq: int, variant, data: bytes) -> None:
        with self._cond:
            if self._frame is None or self._frame.seq != seq:
                return  # stale encode of an older frame
            if self._encoded_seq != seq:
                self._encoded_seq, self._encoded = seq, {}
            if len(self._encoded) < 8:  # bounded: at most 8 variants per frame
                self._encoded[variant] = data

    def clear(self) -> None:
        with self._cond:
            self._frame = None
            self._encoded = {}
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._frame = None
            self._encoded = {}
            self._cond.notify_all()


class FrameQueue:
    """Bounded, thread-safe, drop-oldest queue."""

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._items: deque[Frame] = deque(maxlen=maxsize)
        self._cond = threading.Condition()
        self.dropped = 0
        self.maxsize = maxsize

    def put(self, frame: Frame) -> None:
        with self._cond:
            if len(self._items) == self.maxsize:
                self.dropped += 1  # deque(maxlen) evicts the oldest on append
            self._items.append(frame)
            self._cond.notify()

    def get(self, timeout: float | None = None) -> Frame | None:
        with self._cond:
            if not self._items and not self._cond.wait_for(lambda: bool(self._items), timeout):
                return None
            return self._items.popleft()

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)
