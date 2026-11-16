from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# SQLite (development) returns naive datetimes; they are UTC. Always serialize with an offset so
# browsers never read them as local time.
UTCDateTime = Annotated[datetime, AfterValidator(lambda d: d if d.tzinfo else d.replace(tzinfo=UTC))]

# ---------------------------------------------------------------------------- device-facing (agent)


class RegisterIn(BaseModel):
    device_id: uuid.UUID
    device_name: str = Field(min_length=1, max_length=120)
    agent_version: str | None = Field(default=None, max_length=40)
    hardware: dict[str, Any] = Field(default_factory=dict)


class RegisterOut(BaseModel):
    device_id: uuid.UUID
    device_secret: str
    heartbeat_interval_s: int


class HeartbeatOut(BaseModel):
    config_version: int
    heartbeat_interval_s: int
    rotate_credentials: bool
    server_time: str
    status: str


class RotateOut(BaseModel):
    device_secret: str


class ConfigDocumentOut(BaseModel):
    version: int
    config: dict[str, Any]
    checksum: str


class ConfigAckIn(BaseModel):
    version: int = Field(ge=1)
    status: Literal["applied", "partial", "failed"]
    applied: list[str] = Field(default_factory=list, max_length=100)
    ignored: list[str] = Field(default_factory=list, max_length=100)
    errors: dict[str, str] = Field(default_factory=dict)


class EventBatchIn(BaseModel):
    device_id: uuid.UUID | None = None
    events: list[Any]


class EventBatchOut(BaseModel):
    accepted: list[str]
    duplicate: list[str]
    rejected: list[dict[str, Any]]


# ---------------------------------------------------------------------------- user-facing (dashboard)


class DeviceGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class DeviceGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    device_count: int = 0


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    group_id: uuid.UUID | None = None
    model_config = ConfigDict(extra="forbid")


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    external_id: str | None
    name: str
    location: str | None
    source_type: str
    status: str
    is_enabled: bool
    ai_enabled: bool | None
    fps: float | None
    resolution_width: int | None
    resolution_height: int | None
    last_error: str | None
    last_frame_at: UTCDateTime | None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    group_id: uuid.UUID | None
    group_name: str | None = None
    status: str
    status_reason: str | None
    status_changed_at: UTCDateTime | None
    agent_version: str | None
    last_heartbeat_at: UTCDateTime | None
    last_seen_ip: str | None
    heartbeat_interval_s: int
    ai_status: dict[str, Any]
    camera_status: dict[str, Any]
    sync_status: dict[str, Any]
    desired_config_version: int
    applied_config_version: int
    registered_at: UTCDateTime | None
    revoked_at: UTCDateTime | None


class DeviceDetailOut(DeviceOut):
    hardware: dict[str, Any]
    metrics: dict[str, Any]
    cameras: list[CameraOut] = Field(default_factory=list)
    rotate_credentials: bool = False


class EnrollmentTokenIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    group_id: uuid.UUID | None = None
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    max_uses: int = Field(default=1, ge=1, le=1000)


class EnrollmentTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str | None
    group_id: uuid.UUID | None
    created_at: UTCDateTime
    expires_at: UTCDateTime
    max_uses: int
    uses: int
    revoked_at: UTCDateTime | None


class EnrollmentTokenCreated(EnrollmentTokenOut):
    token: str = Field(description="Shown once. Give it to the edge agent (aci-agentctl register).")


class ConfigPatchIn(BaseModel):
    base_version: int = Field(ge=0, description="Version the change is based on (optimistic concurrency)")
    config: dict[str, Any] = Field(description="JSON merge patch (null removes a key), or full document with replace")
    replace: bool = False
    comment: str | None = Field(default=None, max_length=300)


class ConfigRollbackIn(BaseModel):
    to_version: int = Field(ge=1)
    base_version: int = Field(ge=0)
    comment: str | None = Field(default=None, max_length=300)


class ConfigVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    config: dict[str, Any]
    checksum: str
    comment: str | None
    rollback_of: int | None
    created_by: uuid.UUID | None
    created_at: UTCDateTime
    status: str
    acknowledged_at: UTCDateTime | None
    ack: dict[str, Any]


class ConfigStateOut(BaseModel):
    version: int
    config: dict[str, Any]
    checksum: str
    status: str | None
    applied_version: int
    acknowledged_at: UTCDateTime | None
    ack: dict[str, Any] = Field(default_factory=dict)


class HeartbeatOutRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    received_at: UTCDateTime
    status: str
    agent_version: str | None
    cameras_online: int
    cameras_total: int
    ai_mode: str | None
    queue_pending: int
    metrics: dict[str, Any]


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    status: str
    received: int
    accepted: int
    duplicate: int
    rejected: int
    duration_ms: int | None
    details: dict[str, Any]
    created_at: UTCDateTime


class EventOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    camera_id: uuid.UUID | None
    camera_ref: str | None
    event_type: str
    source: str
    occurred_at: UTCDateTime
    received_at: UTCDateTime
    zone_id: str | None
    line_id: str | None
    track_id: str | None
    metadata: dict[str, Any]


class EventsPageOut(BaseModel):
    items: list[EventOut]
    next_cursor: str | None


class EventStatOut(BaseModel):
    bucket_start: UTCDateTime
    device_id: uuid.UUID
    camera_ref: str
    event_type: str
    count: int
