"""Analytics Service — statistics computed from real, stored events (MVP 08).

Independent of the HTTP layer: give it a database session and a query, get plain dicts back.

Rules the numbers follow (the dashboard shows these as "method / limitations"):

* **Standard (motion) analytics never count people.** They count motion episodes, moving-object
  tracks entering zones / crossing lines, and activity time. A "motion object" may be a person, a
  car, a shadow or a tree.
* **People analytics come only from the AI engine** and are reported only when AI person events
  exist in the period. One *observation* = one temporary track on one camera (never one per
  frame). A person who leaves and comes back, or is seen by two cameras, is counted again —
  temporary track IDs do not identify anybody.
* **Inbound / outbound** = AI person tracks crossing a counting line forward / backward (the
  direction the line was drawn in).
* **Zone occupancy** is an *estimate*: entries − exits of motion objects in the period, never below
  zero; tracks that are lost inside a zone make it drift.
* Time buckets use the viewer's time zone (IANA name), daylight-saving aware.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Camera, CameraEvent, DeviceHeartbeat, EdgeDevice
from app.processing.event_processor import PERSON_EVENT_TYPES

MAX_RANGE_DAYS = 400
MAX_EVENTS = 500_000
STANDARD_TYPES = ("motion_started", "motion_ended", "camera_motion_activity", "object_entered_zone",
                  "object_exited_zone", "line_crossed")  # fmt: skip
HEALTH_TYPES = ("camera_error", "camera_recovered", "ai_error", "ai_recovered", "device_offline", "device_error",
                "device_online")  # fmt: skip

LIMITATIONS = {
    "motion": "Motion analytics count motion episodes and moving objects, not people. Any moving object "
    "(person, vehicle, shadow, foliage) is included.",
    "people": "People counts come from the AI engine only. One observation is one temporary track on one camera; "
    "a person who re-enters or is seen by several cameras is counted again (no identification).",
    "lines": "Inbound/outbound is the crossing direction relative to how the line was drawn (forward = inbound).",
    "zones": "Occupancy is an estimate (entries − exits in the period, minimum 0). Tracks lost inside a zone "
    "make it drift; use it as a trend, not an exact count.",
    "health": "Downtime is measured from an error event to the next recovery event of the same camera; open errors "
    "count until the end of the period.",
}


@dataclass(frozen=True)
class Query:
    since: datetime
    until: datetime
    tz: ZoneInfo
    device_id: uuid.UUID | None = None
    camera_ref: str | None = None

    @property
    def hours(self) -> float:
        return max((self.until - self.since).total_seconds() / 3600, 1 / 60)


def make_query(
    since: datetime | None,
    until: datetime | None,
    tz: str | None,
    device_id: uuid.UUID | None = None,
    camera_ref: str | None = None,
) -> Query:
    try:
        zone = ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise AppError(f"Unknown time zone '{(tz or '')[:40]}'", code="invalid_timezone") from None
    until = (until or datetime.now(UTC)).astimezone(UTC)
    since = (since or until - timedelta(days=1)).astimezone(UTC)
    if since >= until:
        raise AppError("'since' must be before 'until'", code="invalid_range")
    if until - since > timedelta(days=MAX_RANGE_DAYS):
        raise AppError(f"Range longer than {MAX_RANGE_DAYS} days", code="invalid_range")
    return Query(since=since, until=until, tz=zone, device_id=device_id, camera_ref=camera_ref)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _events(db: Session, q: Query, types: tuple[str, ...]) -> list[tuple]:
    conds = [CameraEvent.occurred_at >= q.since, CameraEvent.occurred_at < q.until, CameraEvent.event_type.in_(types)]
    if q.device_id:
        conds.append(CameraEvent.device_id == q.device_id)
    if q.camera_ref:
        conds.append(CameraEvent.camera_ref == q.camera_ref)
    rows = db.execute(
        select(
            CameraEvent.event_type,
            CameraEvent.source,
            CameraEvent.occurred_at,
            CameraEvent.device_id,
            CameraEvent.camera_ref,
            CameraEvent.zone_id,
            CameraEvent.line_id,
            CameraEvent.track_id,
            CameraEvent.event_metadata,
        )  # fmt: skip
        .where(and_(*conds))
        .order_by(CameraEvent.occurred_at)
        .limit(MAX_EVENTS + 1)
    ).all()
    if len(rows) > MAX_EVENTS:
        raise AppError("Too many events in this period — choose a shorter range or a camera", code="range_too_large")
    return rows


def _num(v: Any) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


def _local(q: Query, dt: datetime) -> datetime:
    return _aware(dt).astimezone(q.tz)


def _camera_names(db: Session, keys: set[tuple]) -> dict[tuple, str]:
    devices = {d for d, _ in keys if d}
    if not devices:
        return {}
    rows = db.execute(
        select(Camera.device_id, Camera.external_id, Camera.name).where(Camera.device_id.in_(devices))
    ).all()
    return {(d, e): n for d, e, n in rows}


def _hour_series(q: Query, counter: Counter) -> list[dict]:
    """Every local hour in the range, including empty ones (charts need the zeros)."""
    out = []
    t = _local(q, q.since).replace(minute=0, second=0, microsecond=0)
    end = _local(q, q.until)
    seen = set()
    while t < end:
        key = t.strftime("%Y-%m-%d %H:00")
        if key not in seen:  # DST fall-back repeats a local hour
            seen.add(key)
            out.append({"bucket": key, "value": counter.get(key, 0)})
        t = (t.astimezone(UTC) + timedelta(hours=1)).astimezone(q.tz)
    return out


def _day_series(q: Query, counter: Counter) -> list[dict]:
    out = []
    d = _local(q, q.since).date()
    last = _local(q, q.until - timedelta(microseconds=1)).date()
    while d <= last:
        key = d.isoformat()
        out.append({"bucket": key, "value": counter.get(key, 0)})
        d += timedelta(days=1)
    return out


def standard_analytics(db: Session, q: Query, rows: list[tuple] | None = None) -> dict:
    rows = rows if rows is not None else _events(db, q, STANDARD_TYPES)
    motion = [r for r in rows if r[0] == "motion_started"]
    ended = [r for r in rows if r[0] == "motion_ended"]
    hourly, daily, heat = Counter(), Counter(), Counter()
    per_camera: dict[tuple, Counter] = defaultdict(Counter)
    for r in motion:
        loc = _local(q, r[2])
        hourly[loc.strftime("%Y-%m-%d %H:00")] += 1
        daily[loc.date().isoformat()] += 1
        heat[(loc.weekday(), loc.hour)] += 1
        per_camera[(r[3], r[4])]["motion_events"] += 1
    for r in ended:
        per_camera[(r[3], r[4])]["activity_s"] += _num((r[8] or {}).get("duration_s"))

    zones: dict[tuple, dict] = {}
    lines: dict[tuple, dict] = {}
    for r in rows:
        meta = r[8] or {}
        if r[0] in ("object_entered_zone", "object_exited_zone") and r[5]:
            z = zones.setdefault((r[3], r[4], r[5]), {"name": meta.get("zone_name") or r[5], "entries": 0, "exits": 0,
                                                      "dwell_total_s": 0.0, "dwell_count": 0})  # fmt: skip
            if r[0] == "object_entered_zone":
                z["entries"] += 1
            else:
                z["exits"] += 1
                if "dwell_s" in meta:
                    z["dwell_total_s"] += _num(meta["dwell_s"])
                    z["dwell_count"] += 1
        elif r[0] == "line_crossed" and r[6]:
            ln = lines.setdefault((r[3], r[4], r[6]), {"name": meta.get("line_name") or r[6], "forward": 0,
                                                       "backward": 0})  # fmt: skip
            ln["forward" if meta.get("direction") == "forward" else "backward"] += 1

    names = _camera_names(db, set(per_camera) | {(d, c) for d, c, _ in zones} | {(d, c) for d, c, _ in lines})
    activity_s = sum(_num((r[8] or {}).get("duration_s")) for r in ended)
    return {
        "motion_events": len(motion),
        "activity_duration_s": round(activity_s, 1),
        "motion_frequency_per_hour": round(len(motion) / q.hours, 2),
        "hourly": _hour_series(q, hourly),
        "daily": _day_series(q, daily),
        "heatmap": [{"weekday": d, "hour": h, "value": heat.get((d, h), 0)} for d in range(7) for h in range(24)],
        "zones": sorted(
            (
                {
                    "device_id": str(d) if d else None,
                    "camera_ref": c,
                    "camera_name": names.get((d, c), c),
                    "zone_id": z,
                    "name": v["name"],
                    "entries": v["entries"],
                    "exits": v["exits"],
                    "occupancy_estimate": max(0, v["entries"] - v["exits"]),
                    "activity_duration_s": round(v["dwell_total_s"], 1),
                    "avg_dwell_s": round(v["dwell_total_s"] / v["dwell_count"], 1) if v["dwell_count"] else None,
                }
                for (d, c, z), v in zones.items()
            ),
            key=lambda x: -x["entries"],
        ),  # fmt: skip
        "lines": sorted(
            (
                {
                    "device_id": str(d) if d else None,
                    "camera_ref": c,
                    "camera_name": names.get((d, c), c),
                    "line_id": ln,
                    "name": v["name"],
                    "forward": v["forward"],
                    "backward": v["backward"],
                }
                for (d, c, ln), v in lines.items()
            ),
            key=lambda x: -(x["forward"] + x["backward"]),
        ),  # fmt: skip
        "cameras": sorted(
            (
                {
                    "device_id": str(d) if d else None,
                    "camera_ref": c,
                    "camera_name": names.get((d, c), c),
                    "motion_events": int(v["motion_events"]),
                    "activity_duration_s": round(v["activity_s"], 1),
                }
                for (d, c), v in per_camera.items()
            ),
            key=lambda x: -x["motion_events"],
        ),  # fmt: skip
    }


def ai_analytics(db: Session, q: Query, rows: list[tuple] | None = None) -> dict:
    rows = rows if rows is not None else _events(db, q, PERSON_EVENT_TYPES)
    if not rows:
        return {"available": False, "reason": "No AI person events in this period (AI off or nobody detected)."}
    first_seen: dict[tuple, datetime] = {}
    inbound = outbound = 0
    per_camera: dict[tuple, Counter] = defaultdict(Counter)
    lines: dict[tuple, dict] = {}
    durations = []
    for r in rows:
        key = (r[3], r[4], r[7])
        if r[7] and key not in first_seen:
            first_seen[key] = r[2]
        if r[0] == "person_crossed_line":
            fwd = (r[8] or {}).get("direction") == "forward"
            inbound += fwd
            outbound += not fwd
            per_camera[(r[3], r[4])]["inbound" if fwd else "outbound"] += 1
            ln = lines.setdefault((r[3], r[4], r[6]), {"name": (r[8] or {}).get("line_name") or r[6], "inbound": 0,
                                                       "outbound": 0})  # fmt: skip
            ln["inbound" if fwd else "outbound"] += 1
        elif r[0] == "person_left":
            durations.append(_num((r[8] or {}).get("duration_s")))
    hourly, daily, by_hour, heat = Counter(), Counter(), Counter(), Counter()
    for (dev, cam, _), t in first_seen.items():
        loc = _local(q, t)
        hourly[loc.strftime("%Y-%m-%d %H:00")] += 1
        daily[loc.date().isoformat()] += 1
        by_hour[loc.hour] += 1
        heat[(loc.weekday(), loc.hour)] += 1
        per_camera[(dev, cam)]["observations"] += 1
    names = _camera_names(db, set(per_camera))
    peak = [{"hour": h, "observations": n} for h, n in sorted(by_hour.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return {
        "available": True,
        "people_observations": len(first_seen),
        "inbound": inbound,
        "outbound": outbound,
        "avg_observation_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
        "hourly": _hour_series(q, hourly),
        "daily": _day_series(q, daily),
        "time_distribution": [{"hour": h, "value": by_hour.get(h, 0)} for h in range(24)],
        "heatmap": [{"weekday": d, "hour": h, "value": heat.get((d, h), 0)} for d in range(7) for h in range(24)],
        "peak_hours": peak,
        "lines": [
            {"device_id": str(d) if d else None, "camera_ref": c, "line_id": ln, **v} for (d, c, ln), v in lines.items()
        ],  # fmt: skip
        "cameras": sorted(
            (
                {
                    "device_id": str(d) if d else None,
                    "camera_ref": c,
                    "camera_name": names.get((d, c), c),
                    "observations": int(v["observations"]),
                    "inbound": int(v["inbound"]),
                    "outbound": int(v["outbound"]),
                }
                for (d, c), v in per_camera.items()
            ),
            key=lambda x: -x["observations"],
        ),  # fmt: skip
    }


def health_analytics(db: Session, q: Query, rows: list[tuple] | None = None) -> dict:
    rows = rows if rows is not None else _events(db, q, HEALTH_TYPES)
    counts = Counter(r[0] for r in rows)
    open_since: dict[tuple, datetime] = {}
    downtime: Counter = Counter()
    for r in rows:
        key = (r[3], r[4])
        if r[0] == "camera_error":
            open_since.setdefault(key, _aware(r[2]))
        elif r[0] == "camera_recovered" and key in open_since:
            downtime[key] += (_aware(r[2]) - open_since.pop(key)).total_seconds()
    for key, start in open_since.items():
        downtime[key] += (q.until - start).total_seconds()
    names = _camera_names(db, set(downtime))
    return {
        "counts": dict(counts),
        "camera_downtime": sorted(
            (
                {
                    "device_id": str(d) if d else None,
                    "camera_ref": c,
                    "camera_name": names.get((d, c), c),
                    "downtime_s": round(s, 1),
                    "still_failing": (d, c) in open_since,
                }
                for (d, c), s in downtime.items()
            ),
            key=lambda x: -x["downtime_s"],
        ),  # fmt: skip
    }


def ai_performance(db: Session, q: Query) -> dict:
    """From heartbeat history: AI mode distribution, inference FPS and latency per device."""
    conds = [DeviceHeartbeat.received_at >= q.since, DeviceHeartbeat.received_at < q.until]
    if q.device_id:
        conds.append(DeviceHeartbeat.device_id == q.device_id)
    rows = db.execute(select(DeviceHeartbeat.device_id, DeviceHeartbeat.ai_mode, DeviceHeartbeat.metrics)
                      .where(and_(*conds))).all()  # fmt: skip
    names = dict(db.execute(select(EdgeDevice.id, EdgeDevice.name)).all())
    per_device: dict = defaultdict(lambda: {"heartbeats": 0, "modes": Counter(), "fps": [], "latency": []})
    for dev, mode, metrics in rows:
        d = per_device[dev]
        d["heartbeats"] += 1
        d["modes"][mode or "unknown"] += 1
        ai = (metrics or {}).get("ai") or {}
        if isinstance(ai.get("inference_fps"), int | float):
            d["fps"].append(ai["inference_fps"])
        if isinstance(ai.get("latency_avg_ms"), int | float):
            d["latency"].append(ai["latency_avg_ms"])
    out = []
    for dev, d in per_device.items():
        total = d["heartbeats"]
        out.append({
            "device_id": str(dev), "device_name": names.get(dev), "heartbeats": total,
            "mode_share": {m: round(n / total, 3) for m, n in d["modes"].items()},
            "active_share": round(d["modes"].get("active", 0) / total, 3),
            "avg_inference_fps": round(sum(d["fps"]) / len(d["fps"]), 2) if d["fps"] else None,
            "avg_latency_ms": round(sum(d["latency"]) / len(d["latency"]), 1) if d["latency"] else None,
            "max_latency_ms": max(d["latency"]) if d["latency"] else None,
        })  # fmt: skip
    return {"devices": sorted(out, key=lambda x: x["device_name"] or ""), "heartbeats": len(rows)}


def full_report(db: Session, q: Query) -> dict:
    rows = _events(db, q, STANDARD_TYPES + PERSON_EVENT_TYPES + HEALTH_TYPES)
    std = [r for r in rows if r[0] in STANDARD_TYPES]
    ppl = [r for r in rows if r[0] in PERSON_EVENT_TYPES]
    hlt = [r for r in rows if r[0] in HEALTH_TYPES]
    return {
        "range": {
            "since": q.since.isoformat(),
            "until": q.until.isoformat(),
            "timezone": str(q.tz.key),
            "hours": round(q.hours, 2),
        },  # fmt: skip
        "filters": {"device_id": str(q.device_id) if q.device_id else None, "camera_ref": q.camera_ref},
        "standard": standard_analytics(db, q, std),
        "ai": ai_analytics(db, q, ppl),
        "health": health_analytics(db, q, hlt),
        "limitations": LIMITATIONS,
        "generated_at": datetime.now(UTC).isoformat(),
    }
