"""Aggregated views for the dashboard: overview counters and anonymous observations.

All numbers are computed here from the database — the dashboard only displays them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select

from app.api.deps import CurrentUser, DbDep, require_permission
from app.core.permissions import has
from app.models import Camera, CameraEvent, EdgeDevice, EventStatistic, User
from app.processing.event_processor import EVENT_CATEGORIES, PERSON_EVENT_TYPES
from app.services import observations, system_settings

router = APIRouter()
CanReadObservations = Annotated[User, Depends(require_permission("observations:read"))]

ACTIVE_DEVICE_STATES = ("online", "synchronizing", "degraded")


@router.get("/dashboard/overview")
def overview(
    user: CurrentUser,
    db: DbDep,
    since: Annotated[datetime | None, Query(description="Start of 'today' in the viewer's time zone")] = None,
):
    """Fleet-wide counters. Sections the user may not see are ``null``."""
    perms = list(user.role.permissions or [])
    since = since or datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict = {"since": since.isoformat(), "generated_at": datetime.now(UTC).isoformat()}

    if has(perms, "cameras:read"):
        rows = dict(
            db.execute(
                select(Camera.status, func.count()).where(Camera.device_id.is_not(None)).group_by(Camera.status)
            ).all()
        )
        total = sum(rows.values())
        out["cameras"] = {"total": total, "online": rows.get("online", 0), "offline": total - rows.get("online", 0),
                          "by_status": rows}  # fmt: skip
    else:
        out["cameras"] = None

    if has(perms, "devices:read"):
        devices = db.execute(select(EdgeDevice.status, EdgeDevice.ai_status)).all()
        by_status: dict[str, int] = {}
        ai_active = ai_off = ai_problem = 0
        for status, ai in devices:
            by_status[status] = by_status.get(status, 0) + 1
            if status in ("revoked",):
                continue
            ai = ai or {}
            if not ai.get("enabled"):
                ai_off += 1
            elif ai.get("mode") == "active" and status in ACTIVE_DEVICE_STATES:
                ai_active += 1
            else:
                ai_problem += 1
        out["devices"] = {
            "total": sum(v for k, v in by_status.items() if k != "revoked"),
            "active": sum(by_status.get(s, 0) for s in ACTIVE_DEVICE_STATES),
            "by_status": by_status,
            "ai_active": ai_active,
            "without_ai": ai_off,
            "ai_not_running": ai_problem,
        }
    else:
        out["devices"] = None

    if has(perms, "events:read"):
        bucket = since.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        counts = dict(
            db.execute(
                select(EventStatistic.event_type, func.sum(EventStatistic.count))
                .where(EventStatistic.bucket_start >= bucket)
                .group_by(EventStatistic.event_type)
            ).all()
        )
        # Motion events and person (AI) observations are counted separately, never mixed.
        out["events_today"] = {
            "motion": int(sum(counts.get(t, 0) for t in ("motion_started",))),
            "zone": int(sum(counts.get(t, 0) for t in EVENT_CATEGORIES["zone"])),
            "line": int(counts.get("line_crossed", 0)),
            "person_events": int(sum(counts.get(t, 0) for t in PERSON_EVENT_TYPES)),
            "by_type": {k: int(v) for k, v in counts.items()},
        }
        recent = db.execute(
            select(CameraEvent, Camera.name, EdgeDevice.name)
            .outerjoin(Camera, Camera.id == CameraEvent.camera_id)
            .outerjoin(EdgeDevice, EdgeDevice.id == CameraEvent.device_id)
            .order_by(CameraEvent.occurred_at.desc())
            .limit(10)
        ).all()
        out["recent_events"] = [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "source": e.source,
                "occurred_at": (
                    e.occurred_at if e.occurred_at.tzinfo else e.occurred_at.replace(tzinfo=UTC)
                ).isoformat(),
                "camera_name": cam,
                "camera_ref": e.camera_ref,
                "device_name": dev,
            }  # fmt: skip
            for e, cam, dev in recent
        ]
    else:
        out["events_today"] = None
        out["recent_events"] = None

    if has(perms, "observations:read"):
        key = (CameraEvent.device_id, CameraEvent.camera_ref, CameraEvent.track_id)
        sub = (
            select(*key)
            .where(
                and_(
                    CameraEvent.event_type.in_(PERSON_EVENT_TYPES),
                    CameraEvent.track_id.is_not(None),
                    CameraEvent.occurred_at >= since,
                )
            )  # fmt: skip
            .group_by(*key)
            .subquery()
        )
        out["observations_today"] = db.scalar(select(func.count()).select_from(sub)) or 0
    else:
        out["observations_today"] = None
    return out


@router.get("/cameras")
def list_cameras(_: Annotated[User, Depends(require_permission("cameras:read"))], db: DbDep):
    """Camera registry across all devices (reported in heartbeats; no stream URLs or credentials)."""
    rows = db.execute(
        select(Camera, EdgeDevice.name, EdgeDevice.status)
        .join(EdgeDevice, EdgeDevice.id == Camera.device_id)
        .order_by(EdgeDevice.name, Camera.name)
    ).all()
    return [
        {
            "id": str(c.id),
            "external_id": c.external_id,
            "name": c.name,
            "location": c.location,
            "source_type": c.source_type,
            "status": c.status,
            "is_enabled": c.is_enabled,
            "ai_enabled": c.ai_enabled,
            "fps": c.fps,
            "last_error": c.last_error,
            "device_id": str(c.device_id),
            "device_name": dev_name,
            # A camera on an unreachable device has an unknown status, not its last reported one.
            "device_status": dev_status,
        }  # fmt: skip
        for c, dev_name, dev_status in rows
    ]


@router.get("/observations")
def list_observations(
    _: CanReadObservations,
    db: DbDep,
    since: datetime | None = None,
    until: datetime | None = None,
    device_id: uuid.UUID | None = None,
    camera_ref: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    """Anonymous observations: one card per temporary track on one camera (never merged)."""
    privacy = system_settings.load(db)
    since = since or datetime.now(UTC) - timedelta(days=1)
    result = observations.list_observations(
        db, since=since, until=until, device_id=device_id, camera_ref=camera_ref, limit=limit, offset=offset,
        show_attributes=privacy.show_person_attributes,
    )  # fmt: skip
    return result | {"limit": limit, "offset": offset, "since": since.isoformat()}
