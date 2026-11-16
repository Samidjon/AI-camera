from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aci_agent.camera.manager import CameraView, TestResult
from aci_agent.camera.models import CameraStatus, FailureKind, SourceType

_FORBID = ConfigDict(extra="forbid")


class CameraOptions(BaseModel):
    model_config = _FORBID

    max_fps: float | None = Field(default=None, ge=0.5, le=120, description="Cap on frames delivered to consumers")
    loop: bool | None = Field(default=None, description="File sources: restart at end of file")
    onvif_profile: int | None = Field(default=None, ge=0, le=16, description="ONVIF media profile index")


class CameraCreate(BaseModel):
    model_config = _FORBID

    name: str = Field(min_length=1, max_length=120)
    source_type: SourceType
    source_uri: str = Field(
        min_length=1, max_length=1024, description="RTSP/stream URL, ONVIF host[:port], USB index or file path"
    )
    location: str | None = Field(default=None, max_length=255)
    group_id: str | None = None
    is_enabled: bool = True
    options: CameraOptions = Field(default_factory=CameraOptions)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    connect: bool = Field(default=True, description="Start streaming right after creation")


class CameraUpdate(BaseModel):
    """Partial update. Credentials: omit to keep, send values to replace, or set clear_credentials."""

    model_config = _FORBID

    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_type: SourceType | None = None
    source_uri: str | None = Field(default=None, min_length=1, max_length=1024)
    location: str | None = Field(default=None, max_length=255)
    group_id: str | None = None
    is_enabled: bool | None = None
    options: CameraOptions | None = None
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    clear_credentials: bool = False


class CameraTestRequest(BaseModel):
    model_config = _FORBID

    source_type: SourceType
    source_uri: str = Field(min_length=1, max_length=1024)
    options: CameraOptions = Field(default_factory=CameraOptions)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)


class Resolution(BaseModel):
    width: int
    height: int


class CameraOut(BaseModel):
    id: str
    name: str
    location: str | None
    group_id: str | None
    source_type: SourceType
    source: str = Field(description="Credential-free source description")
    has_credentials: bool
    is_enabled: bool
    options: dict
    status: CameraStatus
    fps: float | None = Field(description="Measured from received frames")
    nominal_fps: float | None = Field(description="Advertised by the source, if any")
    resolution: Resolution | None
    last_connected_at: datetime | None
    last_frame_at: datetime | None
    last_error: str | None
    failure_kind: FailureKind | None
    reconnect_attempts: int
    frames_received: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_view(cls, view: CameraView) -> CameraOut:
        c, rt = view.config, view.runtime
        return cls(
            id=c.id,
            name=c.name,
            location=c.location,
            group_id=c.group_id,
            source_type=c.source_type,
            source=f"usb:{c.source_uri}" if c.source_type == SourceType.USB else c.source_uri,
            has_credentials=view.has_credentials,
            is_enabled=c.is_enabled,
            options=c.options,
            status=rt.status,
            fps=rt.fps,
            nominal_fps=rt.nominal_fps,
            resolution=Resolution(width=rt.width, height=rt.height) if rt.width and rt.height else None,
            last_connected_at=rt.last_connected_at,
            last_frame_at=rt.last_frame_at,
            last_error=rt.last_error,
            failure_kind=rt.failure_kind,
            reconnect_attempts=rt.reconnect_attempts,
            frames_received=rt.frames_received,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class TestResultOut(BaseModel):
    ok: bool
    message: str
    failure_kind: FailureKind | None
    resolution: Resolution | None
    nominal_fps: float | None
    latency_ms: float | None
    stream: str | None

    @classmethod
    def from_result(cls, r: TestResult) -> TestResultOut:
        return cls(
            ok=r.ok,
            message=r.message,
            failure_kind=r.failure,
            resolution=Resolution(width=r.width, height=r.height) if r.width and r.height else None,
            nominal_fps=r.nominal_fps,
            latency_ms=r.latency_ms,
            stream=r.stream,
        )


class GroupIn(BaseModel):
    model_config = _FORBID

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None
    camera_count: int = 0
    created_at: datetime
    updated_at: datetime


class StreamTokenOut(BaseModel):
    token: str
    expires_in: int
    stream_url: str
    snapshot_url: str


class AgentStatusOut(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    auth_configured: bool
    database_ok: bool
    cameras_total: int
    cameras_by_status: dict[str, int]
    active_workers: int
    leaked_workers: int
