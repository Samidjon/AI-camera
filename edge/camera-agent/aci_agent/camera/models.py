"""Camera domain types shared by storage, workers, manager and API."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class SourceType(enum.StrEnum):
    RTSP = "rtsp"
    ONVIF = "onvif"
    USB = "usb"
    FILE = "file"
    FFMPEG = "ffmpeg"


class CameraStatus(enum.StrEnum):
    ONLINE = "online"  # frames are actually arriving
    OFFLINE = "offline"  # not connected (stopped by operator, or file ended)
    CONNECTING = "connecting"  # first connection attempt in progress
    RECONNECTING = "reconnecting"  # stream lost / stalled, retrying with backoff
    ERROR = "error"  # non-transient failure (auth, missing file, unsupported format); still retried slowly
    DISABLED = "disabled"  # disabled in configuration


class FailureKind(enum.StrEnum):
    """Why a connection failed. Drives status (ERROR vs RECONNECTING) and operator messages."""

    UNREACHABLE = "unreachable"  # host down / cable unplugged / network lost / timeout
    AUTH_FAILED = "auth_failed"
    NOT_FOUND = "not_found"  # wrong RTSP path, missing file, missing USB device
    INVALID_SOURCE = "invalid_source"  # malformed URL / config
    UNSUPPORTED_FORMAT = "unsupported_format"  # opens but frames can't be decoded
    STREAM_LOST = "stream_lost"  # was online, frames stopped
    UNKNOWN = "unknown"

    @property
    def is_transient(self) -> bool:
        return self in {FailureKind.UNREACHABLE, FailureKind.STREAM_LOST, FailureKind.UNKNOWN}


@dataclass(slots=True)
class CameraSecret:
    """Connection credentials. Only ever stored encrypted; never returned by the API or logged."""

    username: str = ""
    password: str = ""

    def __bool__(self) -> bool:
        return bool(self.username or self.password)

    def __repr__(self) -> str:  # never leak in logs / tracebacks
        return f"CameraSecret(username={'set' if self.username else 'unset'}, password=***)"


@dataclass(slots=True)
class CameraConfig:
    id: str
    name: str
    source_type: SourceType
    # Credential-free source: RTSP/FFmpeg URL, ONVIF "host:port", USB index, or file path.
    source_uri: str
    location: str | None = None
    group_id: str | None = None
    is_enabled: bool = True
    # Per-source options: loop (file), backend (usb), onvif_profile, max_fps, rtsp_transport.
    options: dict = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class CameraRuntime:
    """Live state reported by a worker. Status reflects real frame flow, not just an opened socket."""

    status: CameraStatus = CameraStatus.OFFLINE
    fps: float | None = None  # measured from received frames
    nominal_fps: float | None = None  # what the source advertises (may be absent)
    width: int | None = None
    height: int | None = None
    last_connected_at: datetime | None = None
    last_frame_at: datetime | None = None
    last_error: str | None = None
    failure_kind: FailureKind | None = None
    reconnect_attempts: int = 0
    frames_received: int = 0
    frames_dropped: int = 0
