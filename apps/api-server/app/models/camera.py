from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CameraSourceType(enum.StrEnum):
    RTSP = "rtsp"
    ONVIF = "onvif"
    USB = "usb"
    FILE = "file"
    FFMPEG = "ffmpeg"


class CameraStatus(enum.StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    DISABLED = "disabled"


def _in(values: type[enum.StrEnum]) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class CameraGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "camera_groups"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    cameras: Mapped[list[Camera]] = relationship(back_populates="group")


class Camera(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Camera metadata. Connection secrets (RTSP credentials) are NOT stored here —
    they get a separate encrypted store in MVP 02."""

    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint(f"source_type IN ({_in(CameraSourceType)})", name="source_type_valid"),
        UniqueConstraint("device_id", "external_id", name="uq_cameras_device_id_external_id"),
        CheckConstraint(f"status IN ({_in(CameraStatus)})", name="status_valid"),
        CheckConstraint("fps IS NULL OR fps >= 0", name="fps_non_negative"),
        CheckConstraint(
            "(resolution_width IS NULL OR resolution_width > 0) AND "
            "(resolution_height IS NULL OR resolution_height > 0)",
            name="resolution_positive",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CameraStatus.OFFLINE.value, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("camera_groups.id", ondelete="SET NULL"), index=True)
    resolution_width: Mapped[int | None] = mapped_column(Integer)
    resolution_height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # MVP 06: cameras are reported by edge devices in heartbeats (registry synced from the agents).
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("edge_devices.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))  # agent-local camera id
    ai_enabled: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(String(300))

    group: Mapped[CameraGroup | None] = relationship(back_populates="cameras")
