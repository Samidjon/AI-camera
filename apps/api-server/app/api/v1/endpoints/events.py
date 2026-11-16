"""Events: batch upload from edge agents (device-facing) and queries (users)."""

from __future__ import annotations

import base64
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, or_, select

from app.api.deps import DbDep, SettingsDep, require_permission
from app.api.device_auth import CurrentDevice
from app.core.errors import AppError, NotFoundError, PermissionDeniedError
from app.core.ratelimit import limiter
from app.models import CameraEvent, EventStatistic, SynchronizationLog, User
from app.realtime.hub import hub
from app.schemas.devices import EventBatchIn, EventBatchOut, EventOut, EventsPageOut, EventStatOut

router = APIRouter()
CanReadEvents = Annotated[User, Depends(require_permission("events:read"))]


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


def _event_out(e: CameraEvent) -> EventOut:
    def aw(dt):
        return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)

    return EventOut(
        id=e.id, device_id=e.device_id, camera_id=e.camera_id, camera_ref=e.camera_ref, event_type=e.event_type,
        source=e.source, occurred_at=aw(e.occurred_at), received_at=aw(e.received_at), zone_id=e.zone_id,
        line_id=e.line_id, track_id=e.track_id, metadata=e.event_metadata or {},
    )  # fmt: skip


@router.post("/batch", response_model=EventBatchOut)
def upload_batch(body: EventBatchIn, request: Request, device: CurrentDevice, db: DbDep, settings: SettingsDep):
    """Idempotent batch upload. Per event: ``accepted`` (stored now), ``duplicate`` (already stored —
    safe to drop from the device queue) or ``rejected`` (invalid; will never be accepted)."""
    limiter.enforce(f"events:{device.id}", settings.rate_events_per_s, settings.rate_events_burst)
    if body.device_id is not None and body.device_id != device.id:
        raise PermissionDeniedError("Batch device_id does not match the authenticated device")
    if len(body.events) > settings.events_batch_max:
        raise PayloadTooLarge(f"At most {settings.events_batch_max} events per batch")
    started = time.perf_counter()
    result = request.app.state.event_processor.process(db, device, body.events)
    status = "ok" if not result.rejected else ("partial" if result.accepted or result.duplicate else "failed")
    db.add(
        SynchronizationLog(
            device_id=device.id, kind="events", status=status, received=len(body.events),
            accepted=len(result.accepted), duplicate=len(result.duplicate), rejected=len(result.rejected),
            duration_ms=int((time.perf_counter() - started) * 1000),
            details={"rejected": result.rejected[:10]} if result.rejected else {},
        )
    )  # fmt: skip
    db.commit()  # publish only what is durably stored
    if result.stored:
        hub.publish(
            "event.created",
            {"device_id": str(device.id), "device_name": device.name, "count": len(result.stored),
             "events": result.stored[:20]},
        )  # fmt: skip
    hub.publish("sync.status", {"device_id": str(device.id), "kind": "events", "status": status,
                                "accepted": len(result.accepted), "duplicate": len(result.duplicate),
                                "rejected": len(result.rejected)})  # fmt: skip
    return result.as_response()


def _encode_cursor(e: CameraEvent) -> str:
    occurred = e.occurred_at if e.occurred_at.tzinfo else e.occurred_at.replace(tzinfo=UTC)
    return base64.urlsafe_b64encode(f"{occurred.isoformat()}|{e.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        when, ident = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(when), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError):
        raise AppError("Invalid cursor", code="invalid_cursor") from None


@router.get("", response_model=EventsPageOut)
def list_events(
    _: CanReadEvents,
    db: DbDep,
    device_id: uuid.UUID | None = None,
    camera_ref: Annotated[str | None, Query(max_length=64)] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    source: Annotated[str | None, Query(pattern="^(standard|ai|system)$")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    q = select(CameraEvent)
    if device_id:
        q = q.where(CameraEvent.device_id == device_id)
    if camera_ref:
        q = q.where(CameraEvent.camera_ref == camera_ref)
    if event_type:
        q = q.where(CameraEvent.event_type.in_(event_type[:20]))
    if source:
        q = q.where(CameraEvent.source == source)
    if since:
        q = q.where(CameraEvent.occurred_at >= since)
    if until:
        q = q.where(CameraEvent.occurred_at < until)
    if cursor:
        when, ident = _decode_cursor(cursor)
        q = q.where(or_(CameraEvent.occurred_at < when, and_(CameraEvent.occurred_at == when, CameraEvent.id < ident)))
    rows = list(db.scalars(q.order_by(CameraEvent.occurred_at.desc(), CameraEvent.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return EventsPageOut(items=[_event_out(e) for e in rows], next_cursor=_encode_cursor(rows[-1]) if more else None)


@router.get("/statistics", response_model=list[EventStatOut])
def statistics(
    _: CanReadEvents,
    db: DbDep,
    since: datetime | None = None,
    until: datetime | None = None,
    device_id: uuid.UUID | None = None,
):
    """Hourly counts maintained by the event processor (default: last 24 h)."""
    since = since or datetime.now(UTC) - timedelta(hours=24)
    q = select(EventStatistic).where(EventStatistic.bucket_start >= since.replace(minute=0, second=0, microsecond=0))
    if until:
        q = q.where(EventStatistic.bucket_start < until)
    if device_id:
        q = q.where(EventStatistic.device_id == device_id)
    return list(db.scalars(q.order_by(EventStatistic.bucket_start).limit(5000)))


@router.get("/statistics/summary")
def statistics_summary(_: CanReadEvents, db: DbDep, since: datetime | None = None):
    since = since or datetime.now(UTC) - timedelta(hours=24)
    rows = db.execute(
        select(EventStatistic.event_type, func.sum(EventStatistic.count))
        .where(EventStatistic.bucket_start >= since.replace(minute=0, second=0, microsecond=0))
        .group_by(EventStatistic.event_type)
    ).all()
    return {"since": since.isoformat(), "counts": {t: int(n) for t, n in rows}}


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, _: CanReadEvents, db: DbDep):
    e = db.get(CameraEvent, event_id)
    if e is None:
        raise NotFoundError("Event not found")
    return _event_out(e)
