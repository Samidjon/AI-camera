from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class EventType(StrEnum):
    MOTION_STARTED = "motion_started"
    MOTION_ENDED = "motion_ended"
    OBJECT_ENTERED_ZONE = "object_entered_zone"
    OBJECT_EXITED_ZONE = "object_exited_zone"
    LINE_CROSSED = "line_crossed"
    CAMERA_MOTION_ACTIVITY = "camera_motion_activity"
    # AI Engine (MVP 04)
    PERSON_DETECTED = "person_detected"
    PERSON_LEFT = "person_left"
    PERSON_CROSSED_LINE = "person_crossed_line"
    # Health (MVP 07, source "system")
    CAMERA_ERROR = "camera_error"
    CAMERA_RECOVERED = "camera_recovered"
    AI_ERROR = "ai_error"
    AI_RECOVERED = "ai_recovered"


@dataclass(slots=True)
class Event:
    """Locally generated event. ``id`` is created here, at the source, so later synchronization to
    the central server (MVP 05/06) is idempotent."""

    camera_id: str
    event_type: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    zone_id: str | None = None
    line_id: str | None = None
    track_id: str | None = None  # temporary motion track id — never a person identifier
    metadata: dict = field(default_factory=dict)
    source: str = "standard"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
