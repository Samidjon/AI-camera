"""Anonymous person observations.

An observation is everything the AI engine reported about **one temporary track on one camera**
(``person_detected`` → ``person_crossed_line``* → ``person_left``). Track IDs are generated per camera
and restart with the tracker; observations are never linked across cameras or over time, and no
face / identity information exists anywhere in the system.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.orm import Session

from app.models import Camera, CameraEvent, EdgeDevice
from app.processing.event_processor import PERSON_EVENT_TYPES


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def list_observations(
    db: Session,
    *,
    since: datetime | None,
    until: datetime | None,
    device_id: uuid.UUID | None,
    camera_ref: str | None,
    limit: int,
    offset: int,
    show_attributes: bool,
) -> dict:
    conds = [CameraEvent.event_type.in_(PERSON_EVENT_TYPES), CameraEvent.track_id.is_not(None)]
    if since:
        conds.append(CameraEvent.occurred_at >= since)
    if until:
        conds.append(CameraEvent.occurred_at < until)
    if device_id:
        conds.append(CameraEvent.device_id == device_id)
    if camera_ref:
        conds.append(CameraEvent.camera_ref == camera_ref)
    key = (CameraEvent.device_id, CameraEvent.camera_ref, CameraEvent.track_id)
    groups_q = (
        select(
            *key,
            func.min(CameraEvent.occurred_at).label("first"),
            func.max(CameraEvent.occurred_at).label("last"),
            func.count().label("n"),
        )  # fmt: skip
        .where(and_(*conds))
        .group_by(*key)
    )
    total = db.scalar(select(func.count()).select_from(groups_q.subquery())) or 0
    groups = db.execute(groups_q.order_by(func.max(CameraEvent.occurred_at).desc()).limit(limit).offset(offset)).all()
    if not groups:
        return {"items": [], "total": total}
    events = db.scalars(
        select(CameraEvent)
        .where(and_(*conds), tuple_(*key).in_([(g[0], g[1], g[2]) for g in groups]))
        .order_by(CameraEvent.occurred_at)
    ).all()
    by_key: dict[tuple, list[CameraEvent]] = {}
    for e in events:
        by_key.setdefault((e.device_id, e.camera_ref, e.track_id), []).append(e)
    device_ids = {g[0] for g in groups if g[0]}
    cam_names = {
        (d, ext): name
        for d, ext, name in db.execute(
            select(Camera.device_id, Camera.external_id, Camera.name).where(Camera.device_id.in_(device_ids))
        ).all()
    }
    dev_names = dict(db.execute(select(EdgeDevice.id, EdgeDevice.name).where(EdgeDevice.id.in_(device_ids))).all())
    items = []
    for dev, cam, track, first, last, n in groups:
        evs = by_key.get((dev, cam, track), [])
        metas = [e.event_metadata or {} for e in evs]
        durations = [m.get("duration_s") for m in metas if isinstance(m.get("duration_s"), int | float)]
        confidences = [m.get("confidence") for m in metas if isinstance(m.get("confidence"), int | float)]
        heading = next((m.get("heading") for m in reversed(metas) if m.get("heading")), None)
        attrs = next((m.get("attributes") for m in reversed(metas) if m.get("attributes")), None)
        first_a, last_a = _aware(first), _aware(last)
        items.append(
            {
                "id": f"{dev}:{cam}:{track}",
                "track_id": track,
                "device_id": str(dev) if dev else None,
                "device_name": dev_names.get(dev),
                "camera_id": cam,
                "camera_name": cam_names.get((dev, cam)),
                "first_seen": first_a.isoformat(),
                "last_seen": last_a.isoformat(),
                "duration_s": round(max(durations), 1) if durations else round((last_a - first_a).total_seconds(), 1),
                "direction": heading,
                "confidence": round(max(confidences), 3) if confidences else None,
                "attributes": attrs if show_attributes else None,
                "attributes_hidden": bool(attrs) and not show_attributes,
                "detector": next((m.get("detector") for m in metas if m.get("detector")), None),
                "events": [e.event_type for e in evs],
                "lines_crossed": [e.line_id for e in evs if e.event_type == "person_crossed_line" and e.line_id],
                "ended": any(e.event_type == "person_left" for e in evs),
                "event_count": n,
            }
        )
    return {"items": items, "total": total}
