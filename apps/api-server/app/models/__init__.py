"""ORM models. Import everything here so Alembic autogenerate sees all tables."""

from app.models.audit import AuditLog, SystemLog
from app.models.camera import Camera, CameraGroup, CameraSourceType, CameraStatus
from app.models.device import (
    ConfigStatus,
    DeviceConfiguration,
    DeviceCredential,
    DeviceGroup,
    DeviceHeartbeat,
    DeviceStatus,
    EdgeDevice,
    EnrollmentToken,
    EventStatistic,
    SynchronizationLog,
)
from app.models.event import CameraEvent, EventSource
from app.models.settings import SystemSetting
from app.models.user import Role, User

__all__ = [
    "ConfigStatus",
    "DeviceConfiguration",
    "DeviceCredential",
    "DeviceGroup",
    "DeviceHeartbeat",
    "DeviceStatus",
    "EdgeDevice",
    "EnrollmentToken",
    "EventStatistic",
    "SynchronizationLog",
    "SystemSetting",
    "AuditLog",
    "Camera",
    "CameraEvent",
    "CameraGroup",
    "CameraSourceType",
    "CameraStatus",
    "EventSource",
    "Role",
    "SystemLog",
    "User",
]
