"""Vision configuration (settings, zones, lines), live vision state and the local event log."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from aci_agent.camera.manager import CameraManager, CameraNotFoundError
from aci_agent.events.models import EventType
from aci_agent.events.store import EventStore
from aci_agent.security.auth import Principal
from aci_agent.standard_engine.config import VisionConfig
from aci_agent.standard_engine.pipeline import VisionManager
from aci_agent.storage.vision_repository import VisionRepository

_FORBID = ConfigDict(extra="forbid")


class ZoneIn(BaseModel):
    model_config = _FORBID

    name: str = Field(min_length=1, max_length=80)
    role: Literal["monitor", "exclude"] = "monitor"
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=64, description="Normalized 0..1 points")
    is_enabled: bool = True


class LineIn(BaseModel):
    model_config = _FORBID

    name: str = Field(min_length=1, max_length=80)
    a: tuple[float, float]
    b: tuple[float, float]
    is_enabled: bool = True


def _midnight_utc() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def build_vision_router(
    require, cameras: CameraManager, repo: VisionRepository, vision: VisionManager, store: EventStore
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    CanRead = Annotated[Principal, Depends(require("cameras:read"))]
    CanManage = Annotated[Principal, Depends(require("cameras:manage"))]
    CanReadEvents = Annotated[Principal, Depends(require("events:read"))]

    def _camera(camera_id: str) -> None:
        cameras.get_camera(camera_id)  # raises CameraNotFoundError → 404

    def _state_out(camera_id: str) -> dict:
        st = vision.state(camera_id)
        if st is None:
            return {"status": "not_running"}
        return {
            "status": st.status,
            "motion_active": st.motion_active,
            "motion_since": st.motion_since,
            "objects": len(st.tracks),
            "tracks": [
                {"track_id": t.track_id, "box": t.box, "direction": t.direction, "duration_s": t.duration_s}
                for t in st.tracks
            ],
            "zone_occupancy_estimate": st.zone_occupancy,
            "session_line_counts": st.line_counts,
            "motion_ratio": st.motion_ratio,
            "frames_analysed": st.frames_analysed,
            "frames_skipped": st.frames_skipped,
            "lighting_changes": st.lighting_changes,
            "stream_interruptions": st.stream_interruptions,
            "processing_ms": st.processing_ms,
            "analysis_fps": st.analysis_fps,
            "last_error": st.last_error,
            "updated_at": st.updated_at,
        }

    # ------------------------------------------------------------ settings & state

    @router.get("/cameras/{camera_id}/vision", tags=["vision"])
    def get_vision(camera_id: str, _: CanRead):
        _camera(camera_id)
        return {"config": repo.get_config(camera_id), "state": _state_out(camera_id)}

    @router.put("/cameras/{camera_id}/vision/config", response_model=VisionConfig, tags=["vision"])
    def put_vision_config(camera_id: str, body: VisionConfig, _: CanManage):
        _camera(camera_id)
        saved = repo.save_config(camera_id, body)
        vision.reload(camera_id)
        return saved

    # ------------------------------------------------------------ zones

    @router.get("/cameras/{camera_id}/zones", tags=["vision"])
    def list_zones(camera_id: str, _: CanRead):
        _camera(camera_id)
        occupancy = (vision.state(camera_id) or None) and vision.state(camera_id).zone_occupancy
        return [z | {"occupancy_estimate": (occupancy or {}).get(z["id"])} for z in repo.list_zones(camera_id)]

    @router.post("/cameras/{camera_id}/zones", status_code=201, tags=["vision"])
    def create_zone(camera_id: str, body: ZoneIn, _: CanManage):
        _camera(camera_id)
        zone = repo.save_zone(camera_id, body.name, body.role, body.polygon, body.is_enabled)
        vision.reload(camera_id)
        return zone

    @router.put("/cameras/{camera_id}/zones/{zone_id}", tags=["vision"])
    def update_zone(camera_id: str, zone_id: str, body: ZoneIn, _: CanManage):
        if repo.get_zone(camera_id, zone_id) is None:
            raise CameraNotFoundError(zone_id)
        zone = repo.save_zone(camera_id, body.name, body.role, body.polygon, body.is_enabled, zone_id=zone_id)
        vision.reload(camera_id)
        return zone

    @router.delete("/cameras/{camera_id}/zones/{zone_id}", status_code=204, tags=["vision"])
    def delete_zone(camera_id: str, zone_id: str, _: CanManage):
        if not repo.delete_zone(camera_id, zone_id):
            raise CameraNotFoundError(zone_id)
        vision.reload(camera_id)
        return Response(status_code=204)

    # ------------------------------------------------------------ lines

    @router.get("/cameras/{camera_id}/lines", tags=["vision"])
    def list_lines(camera_id: str, _: CanRead):
        _camera(camera_id)
        today = store.line_counts(camera_id, _midnight_utc())
        return [
            ln | {"counts_today": today.get(ln["id"], {"forward": 0, "backward": 0})}
            for ln in repo.list_lines(camera_id)
        ]

    @router.post("/cameras/{camera_id}/lines", status_code=201, tags=["vision"])
    def create_line(camera_id: str, body: LineIn, _: CanManage):
        _camera(camera_id)
        line = repo.save_line(camera_id, body.name, body.a, body.b, body.is_enabled)
        vision.reload(camera_id)
        return line

    @router.put("/cameras/{camera_id}/lines/{line_id}", tags=["vision"])
    def update_line(camera_id: str, line_id: str, body: LineIn, _: CanManage):
        if repo.get_line(camera_id, line_id) is None:
            raise CameraNotFoundError(line_id)
        line = repo.save_line(camera_id, body.name, body.a, body.b, body.is_enabled, line_id=line_id)
        vision.reload(camera_id)
        return line

    @router.delete("/cameras/{camera_id}/lines/{line_id}", status_code=204, tags=["vision"])
    def delete_line(camera_id: str, line_id: str, _: CanManage):
        if not repo.delete_line(camera_id, line_id):
            raise CameraNotFoundError(line_id)
        vision.reload(camera_id)
        return Response(status_code=204)

    # ------------------------------------------------------------ events

    @router.get("/events", tags=["events"])
    def list_events(
        _: CanReadEvents,
        camera_id: str | None = None,
        event_type: Annotated[list[EventType] | None, Query()] = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = Query(default=None, description="next_cursor from the previous page"),
        limit: int = Query(default=50, ge=1, le=500),
    ):
        before = None
        if cursor:
            occurred, _, eid = cursor.partition("|")
            if not eid:
                raise ValueError("Invalid cursor")
            before = (occurred, eid)
        items = store.query(
            camera_id=camera_id, event_types=[str(t) for t in event_type] if event_type else None,
            since=since, until=until, before=before, limit=limit + 1,
        )  # fmt: skip
        next_cursor = None
        if len(items) > limit:
            items = items[:limit]
            next_cursor = f"{items[-1]['occurred_at']}|{items[-1]['id']}"
        return {"items": items, "next_cursor": next_cursor}

    @router.get("/events/summary", tags=["events"])
    def events_summary(_: CanReadEvents, since: datetime | None = None, camera_id: str | None = None):
        since = since or _midnight_utc()
        return {"since": since, "counts": store.counts_by_type(since, camera_id)}

    return router
