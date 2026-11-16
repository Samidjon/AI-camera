"""Simple multi-object tracker for motion regions (no AI).

Association: centroid distance + bounding-box overlap (IoU), greedy matching on a combined
cost, gated by a maximum distance. Unmatched tracks survive ``max_missed`` analysed frames.

Motion Track IDs are **temporary**: scoped to one camera and one agent session, reset on
restart, and never a person identifier. They only say "the same moving area as a moment ago".
"""

from __future__ import annotations

import itertools
import math
import time
from collections import deque
from dataclasses import dataclass, field

from aci_agent.standard_engine.geometry import Box, Point, compass
from aci_agent.standard_engine.motion import MotionRegion

HISTORY = 32  # centroid points kept per track (bounded)


@dataclass(slots=True)
class MotionTrack:
    track_id: str
    box: Box
    first_seen: float
    last_seen: float
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY))
    hits: int = 1
    missed: int = 0
    max_area: float = 0.0

    @property
    def centroid(self) -> Point:
        return self.box.centroid

    @property
    def duration_s(self) -> float:
        return self.last_seen - self.first_seen

    def direction(self, frame_w: float, frame_h: float) -> str | None:
        """Overall heading over the recent history; ignores jitter below 1 % of the frame."""
        if len(self.history) < 2:
            return None
        (x0, y0), (x1, y1) = self.history[0], self.history[-1]
        dx, dy = x1 - x0, y1 - y0
        if math.hypot(dx / frame_w, dy / frame_h) < 0.01:
            return None
        return compass(dx, dy)

    @property
    def previous_centroid(self) -> Point | None:
        return self.history[-2] if len(self.history) >= 2 else None


@dataclass(slots=True)
class TrackUpdate:
    active: list[MotionTrack]
    started: list[MotionTrack]
    ended: list[MotionTrack]


class MotionTracker:
    def __init__(self, camera_id: str, max_distance: float = 0.15, max_missed: int = 5, min_hits: int = 2) -> None:
        self._prefix = f"m-{camera_id[:6]}-"
        self._ids = itertools.count(1)
        self.max_distance = max_distance  # fraction of the frame diagonal
        self.max_missed = max_missed
        self.min_hits = min_hits  # a track is "confirmed" after this many matches (filters flicker)
        self._tracks: dict[str, MotionTrack] = {}
        self._confirmed: set[str] = set()

    def reset(self) -> list[MotionTrack]:
        """Drop all tracks (stream lost / camera reconnected). Returns confirmed tracks that ended."""
        ended = [t for t in self._tracks.values() if t.track_id in self._confirmed]
        self._tracks.clear()
        self._confirmed.clear()
        return ended

    @property
    def tracks(self) -> list[MotionTrack]:
        return [t for t in self._tracks.values() if t.track_id in self._confirmed and t.missed == 0]

    def update(self, regions: list[MotionRegion], frame_w: int, frame_h: int, now: float | None = None) -> TrackUpdate:
        now = time.monotonic() if now is None else now
        diag = math.hypot(frame_w, frame_h)
        gate = self.max_distance * diag

        candidates = []
        for tid, track in self._tracks.items():
            tx, ty = track.centroid
            for i, region in enumerate(regions):
                rx, ry = region.box.centroid
                dist = math.hypot(rx - tx, ry - ty)
                iou = track.box.iou(region.box)
                if dist > gate and iou == 0:
                    continue
                candidates.append((dist / diag - 0.5 * iou, tid, i))
        candidates.sort()

        matched_tracks: set[str] = set()
        matched_regions: set[int] = set()
        for _, tid, i in candidates:
            if tid in matched_tracks or i in matched_regions:
                continue
            track, region = self._tracks[tid], regions[i]
            track.box = region.box
            track.last_seen = now
            track.history.append(region.box.centroid)
            track.hits += 1
            track.missed = 0
            track.max_area = max(track.max_area, region.box.area)
            matched_tracks.add(tid)
            matched_regions.add(i)

        started: list[MotionTrack] = []
        ended: list[MotionTrack] = []
        for tid in list(self._tracks):
            if tid in matched_tracks:
                continue
            track = self._tracks[tid]
            track.missed += 1
            if track.missed > self.max_missed:
                del self._tracks[tid]
                if tid in self._confirmed:
                    self._confirmed.discard(tid)
                    ended.append(track)

        for i, region in enumerate(regions):
            if i in matched_regions:
                continue
            tid = f"{self._prefix}{next(self._ids)}"
            track = MotionTrack(tid, region.box, now, now, max_area=region.box.area)
            track.history.append(region.box.centroid)
            self._tracks[tid] = track

        for track in self._tracks.values():
            if track.track_id not in self._confirmed and track.hits >= self.min_hits:
                self._confirmed.add(track.track_id)
                started.append(track)

        return TrackUpdate(active=self.tracks, started=started, ended=ended)
