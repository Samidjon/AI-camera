"""Zone monitoring and line crossing on top of motion tracks.

All geometry is evaluated in normalized coordinates. A track's reference point is the bottom-
centre of its box (where a moving thing touches the ground), which is what operators intuitively
mean by "entered the zone" / "crossed the line" in a perspective camera view.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from aci_agent.standard_engine.geometry import Box, Point, point_in_polygon, segments_intersect, side_of_line
from aci_agent.tracking.motion_tracker import MotionTrack


@dataclass(slots=True)
class Zone:
    id: str
    name: str
    role: str  # "monitor" | "exclude"
    polygon: list[Point]
    is_enabled: bool = True


@dataclass(slots=True)
class CrossingLine:
    id: str
    name: str
    a: Point
    b: Point
    is_enabled: bool = True


@dataclass(slots=True)
class RuleEvent:
    event_type: str  # object_entered_zone | object_exited_zone | line_crossed
    track_id: str
    zone_id: str | None = None
    line_id: str | None = None
    metadata: dict = field(default_factory=dict)


def anchor(box: Box, frame_w: float, frame_h: float) -> Point:
    """Bottom-centre of the box, normalized."""
    return ((box.x + box.w / 2) / frame_w, min(box.y + box.h, frame_h) / frame_h)


class ZoneMonitor:
    """Tracks which motion tracks are inside each monitor zone; emits enter/exit events."""

    def __init__(self) -> None:
        self._inside: dict[str, set[str]] = {}  # zone_id → track ids
        self._entered_at: dict[tuple[str, str], float] = {}

    def update(
        self, zones: list[Zone], tracks: list[MotionTrack], ended: list[MotionTrack], fw: float, fh: float
    ) -> list[RuleEvent]:
        events: list[RuleEvent] = []
        now = time.monotonic()
        active = {t.track_id: t for t in tracks}
        for zone in zones:
            if zone.role != "monitor" or not zone.is_enabled:
                continue
            inside = self._inside.setdefault(zone.id, set())
            now_inside = {tid for tid, t in active.items() if point_in_polygon(anchor(t.box, fw, fh), zone.polygon)}
            for tid in sorted(now_inside - inside):
                self._entered_at[(zone.id, tid)] = now
                events.append(RuleEvent("object_entered_zone", tid, zone_id=zone.id, metadata={"zone_name": zone.name}))
            # A track that vanished while inside also "exits" (it stopped moving or left the view).
            gone = {t.track_id for t in ended}
            for tid in sorted((inside - now_inside) & (set(active) | gone)):
                dwell = now - self._entered_at.pop((zone.id, tid), now)
                events.append(
                    RuleEvent(
                        "object_exited_zone", tid, zone_id=zone.id,
                        metadata={"zone_name": zone.name, "dwell_s": round(dwell, 1), "track_lost": tid in gone},
                    )
                )  # fmt: skip
            # Tracks missing for a frame or two (not active, not ended) stay "inside" until they
            # reappear elsewhere or end — otherwise they would leave without an exit event.
            pending = (inside - now_inside) - set(active) - gone
            self._inside[zone.id] = now_inside | pending
        known = {z.id for z in zones}
        for zid in list(self._inside):
            if zid not in known:
                del self._inside[zid]
        return events

    def occupancy(self) -> dict[str, int]:
        """Motion objects currently inside each zone — an *estimate*, not a people count."""
        return {zid: len(tids) for zid, tids in self._inside.items()}

    def reset(self) -> None:
        self._inside.clear()
        self._entered_at.clear()


class LineCrossingCounter:
    """Detects a track's anchor point crossing a line segment A→B.

    Direction: ``forward`` = moving along the normal (dy, -dx) of A→B (d = B - A, image
    coordinates, y down) — e.g. for a horizontal line drawn left→right, ``forward`` is upward.
    ``backward`` is the opposite. The dashboard draws this normal as an arrow so operators can
    name the two directions (e.g. "in" / "out").
    """

    def __init__(self, cooldown_s: float = 2.0) -> None:
        self.cooldown_s = cooldown_s
        # (line, track) → (side sign, point) of the last position that was NOT on the line.
        self._last_side: dict[tuple[str, str], tuple[int, Point]] = {}
        self._last_cross: dict[tuple[str, str], float] = {}
        self.counts: dict[str, dict[str, int]] = {}

    def update(
        self, lines: list[CrossingLine], tracks: list[MotionTrack], ended: list[MotionTrack], fw: float, fh: float
    ) -> list[RuleEvent]:
        events: list[RuleEvent] = []
        now = time.monotonic()
        for track in tracks:
            p2 = anchor(track.box, fw, fh)
            for line in lines:
                if not line.is_enabled:
                    continue
                s = side_of_line(line.a, line.b, p2)
                if s == 0:
                    continue  # exactly on the line: wait until it is clearly on one side
                sign = 1 if s > 0 else -1
                key = (line.id, track.track_id)
                prev = self._last_side.get(key)
                self._last_side[key] = (sign, p2)
                # A crossing = the side flipped AND the movement passed through the segment itself
                # (not through its infinite extension).
                if prev is None or prev[0] == sign or not segments_intersect(prev[1], p2, line.a, line.b):
                    continue
                if now - self._last_cross.get(key, -1e9) < self.cooldown_s:
                    continue  # jitter around the line is not a new crossing
                self._last_cross[key] = now
                direction = "forward" if sign < 0 else "backward"
                c = self.counts.setdefault(line.id, {"forward": 0, "backward": 0})
                c[direction] += 1
                events.append(
                    RuleEvent("line_crossed", track.track_id, line_id=line.id,
                              metadata={"line_name": line.name, "direction": direction})
                )  # fmt: skip
        gone = {t.track_id for t in ended}
        if gone:
            self._last_side = {k: v for k, v in self._last_side.items() if k[1] not in gone}
        if len(self._last_cross) > 4096:  # bounded memory
            cutoff = now - self.cooldown_s
            self._last_cross = {k: v for k, v in self._last_cross.items() if v >= cutoff}
        return events

    def reset(self) -> None:
        self._last_side.clear()
        self._last_cross.clear()
