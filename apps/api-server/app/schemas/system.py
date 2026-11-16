from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel


class ComponentState(enum.StrEnum):
    OK = "ok"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"


class OverallState(enum.StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class ComponentHealth(BaseModel):
    state: ComponentState
    latency_ms: float | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    timestamp: datetime


class SystemStatusResponse(BaseModel):
    status: OverallState
    environment: str
    uptime_seconds: float
    started_at: datetime
    timestamp: datetime
    components: dict[str, ComponentHealth]


class VersionResponse(BaseModel):
    name: str
    version: str
    api_version: str
    python_version: str
    environment: str
