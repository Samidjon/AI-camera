from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType


class EventSource(enum.StrEnum):
    STANDARD = "standard"  # Standard Vision Engine (no AI)
    AI = "ai"  # Optional AI Engine
    SYSTEM = "system"  # camera / device health


class CameraEvent(Base):
    """Event produced by an edge agent. `id` is the event UUID generated at the source,
    which makes uploads idempotent (MVP 05/06)."""

    __tablename__ = "camera_events"
    __table_args__ = (
        CheckConstraint("source IN ('standard', 'ai', 'system')", name="source_valid"),
        Index("ix_camera_events_camera_id_occurred_at", "camera_id", "occurred_at"),
        Index("ix_camera_events_event_type_occurred_at", "event_type", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Events outlive camera records for audit/analytics, hence SET NULL.
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=EventSource.STANDARD.value)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    zone_id: Mapped[str | None] = mapped_column(String(64))
    track_id: Mapped[str | None] = mapped_column(String(64))
    # "metadata" is reserved on declarative classes; keep the DB column name.
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
