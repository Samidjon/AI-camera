"""ByteTrack multi-object tracker (re-implementation of Zhang et al., "ByteTrack: Multi-Object
Tracking by Associating Every Detection Box", ECCV 2022) for person detections.

* Constant-velocity Kalman filter on (cx, cy, aspect, h).
* Two-stage association: high-confidence detections first, then low-confidence detections
  recover tracks that would otherwise be lost (occlusion, motion blur).
* Lifecycle: tentative → confirmed (``min_hits``) → lost (kept ``buffer`` frames) → removed.

Track IDs are TEMPORARY: prefixed per camera, counted per agent session, never reused, never
linked across cameras and never derived from appearance — they are not identities.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from aci_agent.ai_engine.providers.base import Detection


class TrackState(StrEnum):
    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    LOST = "lost"
    REMOVED = "removed"


class KalmanFilter:
    """8-state constant velocity model: (cx, cy, a, h, vcx, vcy, va, vh)."""

    _std_pos = 1.0 / 20
    _std_vel = 1.0 / 160

    def __init__(self) -> None:
        self.F = np.eye(8)
        for i in range(4):
            self.F[i, 4 + i] = 1.0
        self.H = np.eye(4, 8)

    def initiate(self, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean = np.r_[z, np.zeros(4)]
        h = z[3]
        std = [
            2 * self._std_pos * h,
            2 * self._std_pos * h,
            1e-2,
            2 * self._std_pos * h,
            10 * self._std_vel * h,
            10 * self._std_vel * h,
            1e-5,
            10 * self._std_vel * h,
        ]
        return mean, np.diag(np.square(std))

    def predict(self, mean: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = mean[3]
        q = np.diag(
            np.square(
                [
                    self._std_pos * h,
                    self._std_pos * h,
                    1e-2,
                    self._std_pos * h,
                    self._std_vel * h,
                    self._std_vel * h,
                    1e-5,
                    self._std_vel * h,
                ]
            )
        )
        return self.F @ mean, self.F @ cov @ self.F.T + q

    def update(self, mean: np.ndarray, cov: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = mean[3]
        r = np.diag(np.square([self._std_pos * h, self._std_pos * h, 1e-1, self._std_pos * h]))
        s = self.H @ cov @ self.H.T + r
        k = cov @ self.H.T @ np.linalg.inv(s)
        mean = mean + k @ (z - self.H @ mean)
        cov = (np.eye(8) - k @ self.H) @ cov
        return mean, cov


def _to_z(d: Detection) -> np.ndarray:
    return np.array([d.x + d.w / 2, d.y + d.h / 2, d.w / max(d.h, 1e-6), d.h], float)


def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a: N×4, b: M×4 in xyxy."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x0 = np.maximum(a[:, None, 0], b[None, :, 0])
    y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2])
    y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def _greedy_match(iou: np.ndarray, threshold: float) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """Greedy max-IoU assignment (adequate for the small per-frame person counts at the edge)."""
    pairs: list[tuple[int, int]] = []
    used_r: set[int] = set()
    used_c: set[int] = set()
    if iou.size:
        order = np.dstack(np.unravel_index(np.argsort(-iou, axis=None), iou.shape))[0]
        for r, c in order:
            if iou[r, c] < threshold:
                break
            if r in used_r or c in used_c:
                continue
            pairs.append((int(r), int(c)))
            used_r.add(int(r))
            used_c.add(int(c))
    return pairs, set(range(iou.shape[0])) - used_r, set(range(iou.shape[1])) - used_c


@dataclass(slots=True)
class PersonTrack:
    track_id: str
    mean: np.ndarray
    cov: np.ndarray
    confidence: float
    first_frame: int
    last_frame: int
    first_ts: float
    last_ts: float
    state: TrackState = TrackState.TENTATIVE
    hits: int = 1
    history: list[tuple[float, float]] = field(default_factory=list)  # bottom-centre points (bounded)
    max_confidence: float = 0.0
    attributes: dict = field(default_factory=dict)

    @property
    def box(self) -> tuple[float, float, float, float]:
        cx, cy, a, h = self.mean[:4]
        w = a * h
        return (cx - w / 2, cy - h / 2, w, h)

    @property
    def xyxy(self) -> np.ndarray:
        x, y, w, h = self.box
        return np.array([x, y, x + w, y + h])

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


@dataclass(slots=True)
class TrackerUpdate:
    confirmed: list[PersonTrack]  # visible, confirmed tracks this frame
    entered: list[PersonTrack]  # newly confirmed this frame
    exited: list[PersonTrack]  # removed this frame (left the view / lost too long)


class ByteTracker:
    def __init__(
        self,
        camera_id: str,
        high_threshold: float = 0.5,
        low_threshold: float = 0.1,
        match_threshold: float = 0.3,
        buffer_frames: int = 30,
        min_hits: int = 3,
    ) -> None:
        self._prefix = f"p-{camera_id[:6]}-"
        self._ids = itertools.count(1)
        self.high = high_threshold
        self.low = low_threshold
        self.match = match_threshold
        self.buffer = buffer_frames
        self.min_hits = min_hits
        self.kf = KalmanFilter()
        self.frame = 0
        self.tracks: list[PersonTrack] = []

    def reset(self) -> list[PersonTrack]:
        ended = [t for t in self.tracks if t.state in (TrackState.CONFIRMED, TrackState.LOST)]
        for t in ended:
            t.state = TrackState.REMOVED
        self.tracks = []
        return ended

    def _apply(self, t: PersonTrack, d: Detection, ts: float) -> None:
        t.mean, t.cov = self.kf.update(t.mean, t.cov, _to_z(d))
        t.confidence = d.confidence
        t.max_confidence = max(t.max_confidence, d.confidence)
        t.last_frame, t.last_ts = self.frame, ts
        t.hits += 1
        x, y, w, h = t.box
        t.history.append((x + w / 2, y + h))
        if len(t.history) > 64:
            del t.history[0]

    def update(self, detections: list[Detection], ts: float) -> TrackerUpdate:
        self.frame += 1
        for t in self.tracks:
            t.mean, t.cov = self.kf.predict(t.mean, t.cov)

        high = [d for d in detections if d.confidence >= self.high]
        low = [d for d in detections if self.low <= d.confidence < self.high]
        active = [t for t in self.tracks if t.state != TrackState.REMOVED]

        # Stage 1: all live tracks vs high-confidence detections.
        iou = _iou_matrix(
            np.array([t.xyxy for t in active]).reshape(-1, 4), np.array([d.xyxy for d in high]).reshape(-1, 4)
        )
        pairs, un_tracks, un_high = _greedy_match(iou, self.match)
        for r, c in pairs:
            self._apply(active[r], high[c], ts)

        # Stage 2: remaining confirmed/lost tracks vs low-confidence detections (the "byte" step).
        rest = [active[i] for i in sorted(un_tracks) if active[i].state != TrackState.TENTATIVE]
        iou2 = _iou_matrix(
            np.array([t.xyxy for t in rest]).reshape(-1, 4), np.array([d.xyxy for d in low]).reshape(-1, 4)
        )
        pairs2, un_rest, _ = _greedy_match(iou2, 0.5)
        for r, c in pairs2:
            self._apply(rest[r], low[c], ts)
        matched_ids = {id(active[r]) for r, _ in pairs} | {id(rest[r]) for r, _ in pairs2}

        entered: list[PersonTrack] = []
        exited: list[PersonTrack] = []
        for t in active:
            if id(t) in matched_ids:
                if t.state == TrackState.LOST:
                    t.state = TrackState.CONFIRMED
                elif t.state == TrackState.TENTATIVE and t.hits >= self.min_hits:
                    t.state = TrackState.CONFIRMED
                    entered.append(t)
                continue
            if t.state == TrackState.TENTATIVE:
                t.state = TrackState.REMOVED  # never confirmed: flicker, no events
            elif t.state == TrackState.CONFIRMED:
                t.state = TrackState.LOST
            if t.state == TrackState.LOST and self.frame - t.last_frame > self.buffer:
                t.state = TrackState.REMOVED
                exited.append(t)

        # New tentative tracks from unmatched high-confidence detections.
        for c in sorted(un_high):
            d = high[c]
            mean, cov = self.kf.initiate(_to_z(d))
            t = PersonTrack(
                f"{self._prefix}{next(self._ids)}",
                mean,
                cov,
                d.confidence,
                self.frame,
                self.frame,
                ts,
                ts,
                max_confidence=d.confidence,
            )
            t.history.append((d.x + d.w / 2, d.y + d.h))
            if self.min_hits <= 1:
                t.state = TrackState.CONFIRMED
                entered.append(t)
            self.tracks.append(t)

        self.tracks = [t for t in self.tracks if t.state != TrackState.REMOVED]
        confirmed = [t for t in self.tracks if t.state == TrackState.CONFIRMED and t.last_frame == self.frame]
        return TrackerUpdate(confirmed, entered, exited)
