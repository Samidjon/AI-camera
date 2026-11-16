"""Events: batch upload from edge agents (device-facing) and queries (users)."""

from __future__ import annotations

import base64
import csv
import io
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Text, and_, cast, func, or_, select

from app.alerts import runtime as alerts
from app.api.deps import DbDep, SettingsDep, require_permission
from app.api.device_auth import CurrentDevice
from app.core.errors import AppError, NotFoundError, PermissionDeniedError
from app.core.ratelimit import limiter
from app.models import Camera, CameraEvent, EdgeDevice, EventStatistic, SynchronizationLog, User
from app.processing.event_processor import EVENT_CATEGORIES
from app.realtime.hub import hub
from app.schemas.devices import EventBatchIn, EventBatchOut, EventOut, EventsPageOut, EventStatOut
from app.services import system_settings

router = APIRouter()
CanReadEvents = Annotated[User, Depends(require_permission("events:read"))]
CanExport = Annotated[User, Depends(require_permission("events:export"))]


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


def _aw(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _event_out(e: CameraEvent, camera_name: str | None = None, device_name: str | None = None) -> EventOut:
    return EventOut(
        id=e.id, device_id=e.device_id, camera_id=e.camera_id, camera_ref=e.camera_ref, event_type=e.event_type,
        source=e.source, occurred_at=_aw(e.occurred_at), received_at=_aw(e.received_at), zone_id=e.zone_id,
        line_id=e.line_id, track_id=e.track_id, metadata=e.event_metadata or {}, camera_name=camera_name,
        device_name=device_name,
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
    privacy = system_settings.load(db)
    result = request.app.state.event_processor.process(
        db, device, body.events, strip_person_attributes=not privacy.store_person_attributes
    )
    alerts.evaluate_events(db, device, result.stored)
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


def _filtered(
    *,
    device_id: uuid.UUID | None,
    camera_ref: str | None,
    event_type: list[str] | None,
    category: list[str] | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    q: str | None,
):
    query = (
        select(CameraEvent, Camera.name, EdgeDevice.name)
        .outerjoin(Camera, Camera.id == CameraEvent.camera_id)
        .outerjoin(EdgeDevice, EdgeDevice.id == CameraEvent.device_id)
    )
    if device_id:
        query = query.where(CameraEvent.device_id == device_id)
    if camera_ref:
        query = query.where(CameraEvent.camera_ref == camera_ref)
    types: set[str] = set(event_type or [])
    for c in category or []:
        if c not in EVENT_CATEGORIES:
            raise AppError(f"Unknown category '{c[:30]}'", code="invalid_category")
        types |= set(EVENT_CATEGORIES[c])
    if types:
        query = query.where(CameraEvent.event_type.in_(sorted(types)[:50]))
    if source:
        query = query.where(CameraEvent.source == source)
    if since:
        query = query.where(CameraEvent.occurred_at >= since)
    if until:
        query = query.where(CameraEvent.occurred_at < until)
    if q and q.strip():
        term = f"%{q.strip().lower()[:100]}%"
        query = query.where(
            or_(
                func.lower(CameraEvent.event_type).like(term),
                func.lower(CameraEvent.camera_ref).like(term),
                func.lower(CameraEvent.zone_id).like(term),
                func.lower(CameraEvent.line_id).like(term),
                func.lower(CameraEvent.track_id).like(term),
                func.lower(Camera.name).like(term),
                func.lower(EdgeDevice.name).like(term),
                func.lower(cast(CameraEvent.event_metadata, Text)).like(term),
            )
        )
    return query


def _filters(
    device_id: uuid.UUID | None = None,
    camera_ref: Annotated[str | None, Query(max_length=64)] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query(description="motion, person, zone, line, camera_error, ...")] = None,
    source: Annotated[str | None, Query(pattern="^(standard|ai|system)$")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search text")] = None,
) -> dict:
    return {"device_id": device_id, "camera_ref": camera_ref, "event_type": event_type, "category": category,
            "source": source, "since": since, "until": until, "q": q}  # fmt: skip


Filters = Annotated[dict, Depends(_filters)]


@router.get("", response_model=EventsPageOut)
def list_events(
    _: CanReadEvents,
    db: DbDep,
    filters: Filters,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    query = _filtered(**filters)
    if cursor:
        when, ident = _decode_cursor(cursor)
        query = query.where(
            or_(CameraEvent.occurred_at < when, and_(CameraEvent.occurred_at == when, CameraEvent.id < ident))
        )
    rows = db.execute(query.order_by(CameraEvent.occurred_at.desc(), CameraEvent.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return EventsPageOut(
        items=[_event_out(e, cam, dev) for e, cam, dev in rows],
        next_cursor=_encode_cursor(rows[-1][0]) if more else None,
    )


CSV_COLUMNS = ["occurred_at", "event_type", "source", "device", "camera", "camera_ref", "zone_id", "line_id",
               "track_id", "received_at", "id", "metadata"]  # fmt: skip
EXPORT_MAX = 100_000


@router.get("/export.csv")
def export_csv(_: CanExport, db: DbDep, filters: Filters):
    """CSV export with the same filters as the list (newest first, at most 100 000 rows)."""
    query = _filtered(**filters).order_by(CameraEvent.occurred_at.desc(), CameraEvent.id.desc()).limit(EXPORT_MAX)
    result = db.execute(query).all()

    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for e, cam, dev in result:
            writer.writerow([
                _aw(e.occurred_at).isoformat(), e.event_type, e.source, dev or "", cam or "", e.camera_ref or "",
                e.zone_id or "", e.line_id or "", e.track_id or "", _aw(e.received_at).isoformat(), str(e.id),
                json.dumps(e.event_metadata or {}, ensure_ascii=False, separators=(",", ":")),
            ])  # fmt: skip
            if buf.tell() > 64_000:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="events-{stamp}.csv"', "Cache-Control": "no-store"},
    )


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
    row = db.execute(
        select(CameraEvent, Camera.name, EdgeDevice.name)
        .outerjoin(Camera, Camera.id == CameraEvent.camera_id)
        .outerjoin(EdgeDevice, EdgeDevice.id == CameraEvent.device_id)
        .where(CameraEvent.id == event_id)
    ).first()
    if row is None:
        raise NotFoundError("Event not found")
    return _event_out(*row)
