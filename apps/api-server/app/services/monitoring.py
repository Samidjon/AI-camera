"""System monitoring (MVP 09): one snapshot of every component's health for the dashboard, probes and
alerting. Values come from the server itself and from the last heartbeat of each edge device —
nothing is guessed: a device that stopped reporting is shown as stale with its last known values."""

from __future__ import annotations

import shutil
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Camera, EdgeDevice
from app.services.health import check_database, check_redis

DISK_WARN_GB = 5.0


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def _state(ok: bool, warn: bool = False) -> str:
    return "error" if not ok else ("warning" if warn else "ok")


def disk_status(path: Path | str = ".") -> dict:
    u = shutil.disk_usage(path)
    free_gb = u.free / 2**30
    return {
        "state": _state(free_gb > 1, free_gb < DISK_WARN_GB),
        "free_gb": round(free_gb, 1),
        "used_percent": round(u.used / u.total * 100, 1),
    }


def snapshot(db: Session, settings: Settings, app_state) -> dict:
    now = datetime.now(UTC)
    database = check_database(settings)
    redis = check_redis(settings)
    devices = list(db.scalars(select(EdgeDevice).where(EdgeDevice.revoked_at.is_(None))))
    by_status = Counter(d.status for d in devices)

    dev_rows, ai_rows, sync_rows = [], [], []
    ai_counts: Counter = Counter()
    for d in devices:
        last = _aware(d.last_heartbeat_at)
        stale = last is None or now - last > timedelta(seconds=max(3 * d.heartbeat_interval_s, 90))
        hw = (d.metrics or {}).get("hardware") or {}
        disk = hw.get("disk") or {}
        dev_rows.append({
            "id": str(d.id), "name": d.name, "status": d.status, "reason": d.status_reason,
            "last_heartbeat_at": last.isoformat() if last else None, "stale": stale,
            "cpu_percent": (hw.get("cpu") or {}).get("percent"),
            "memory_percent": (hw.get("memory") or {}).get("percent"),
            "disk_free_gb": disk.get("free_gb"), "temperature_c": (hw.get("temperature") or {}).get("max_c"),
            "agent_version": d.agent_version,
        })  # fmt: skip
        ai = d.ai_status or {}
        mode = ai.get("mode") or "unknown"
        ai_counts[mode] += 1
        if ai.get("enabled") and mode not in ("active", "disabled"):
            ai_rows.append({"device": d.name, "mode": mode, "reason": ai.get("reason")})
        sync = d.sync_status or {}
        if (sync.get("pending") or 0) > 0 or (sync.get("dropped") or 0) > 0:
            row = {k: sync.get(k) for k in ("pending", "oldest_pending", "dropped")}
            sync_rows.append({"device": d.name, **row})

    cameras = list(db.scalars(select(Camera)))
    cam_status = Counter(c.status for c in cameras if c.is_enabled)
    cam_errors = [{"name": c.name, "status": c.status, "error": c.last_error} for c in cameras
                  if c.is_enabled and c.status in ("error", "offline", "reconnecting")][:50]  # fmt: skip

    offline = by_status.get("offline", 0) + by_status.get("error", 0)
    dropped = sum((r.get("dropped") or 0) for r in sync_rows)
    components = {
        "backend": {
            "state": "ok",
            "uptime_s": round(time.monotonic() - app_state.started_monotonic, 1),
            "event_processor": dict(app_state.event_processor.stats),
        },  # fmt: skip
        "database": {"state": database.state.value, "detail": database.detail, "latency_ms": database.latency_ms},
        "redis": {"state": redis.state.value, "detail": redis.detail},
        "disk": disk_status(),
        "edge_devices": {
            "state": _state(True, offline > 0),
            "total": len(devices),
            "by_status": dict(by_status),
            "devices": dev_rows,
        },  # fmt: skip
        "cameras": {
            "state": _state(True, bool(cam_errors)),
            "total": sum(cam_status.values()),
            "by_status": dict(cam_status),
            "problems": cam_errors,
        },  # fmt: skip
        "ai": {"state": _state(True, bool(ai_rows)), "by_mode": dict(ai_counts), "problems": ai_rows},
        "synchronization": {
            "state": _state(True, dropped > 0 or any((r["pending"] or 0) > 10_000 for r in sync_rows)),
            "devices_with_backlog": sync_rows,
        },  # fmt: skip
    }
    states = {c["state"] for c in components.values()}  # "not_configured" (optional Redis) is not a problem
    worst = "error" if "error" in states else "warning" if states & {"warning", "degraded"} else "ok"
    return {"status": worst, "generated_at": now.isoformat(), "components": components}
