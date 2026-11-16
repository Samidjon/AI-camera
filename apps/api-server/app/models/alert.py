"""Alerts (MVP 08): user-defined rules, raised alerts, notification deliveries."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, JSONType, TimestampMixin, UUIDPrimaryKeyMixin


class AlertKind(enum.StrEnum):
    # Camera
    CAMERA_OFFLINE = "camera_offline"
    CAMERA_RECONNECTED = "camera_reconnected"
    STREAM_ERROR = "stream_error"
    # AI
    AI_OFFLINE = "ai_offline"
    AI_RECOVERED = "ai_recovered"
    MODEL_ERROR = "model_error"
    # Device
    HIGH_CPU = "high_cpu"
    HIGH_MEMORY = "high_memory"
    HIGH_TEMPERATURE = "high_temperature"
    LOW_DISK = "low_disk"
    DEVICE_OFFLINE = "device_offline"
    # Motion
    MOTION_DETECTED = "motion_detected"
    ZONE_ACTIVITY = "zone_activity"
    LINE_CROSSED = "line_crossed"


SEVERITIES = ("info", "warning", "critical")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


class AlertRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(k.value for k in AlertKind)})", name="kind_valid"),
        CheckConstraint(f"severity IN ({_in(SEVERITIES)})", name="severity_valid"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="warning")
    # Metric rules: CPU / memory % and temperature °C fire at or above, low disk fires at or below (GB free).
    threshold: Mapped[float | None] = mapped_column(Float)
    # Optional scope; NULL = every device / camera / zone / line.
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("edge_devices.id", ondelete="CASCADE"))
    camera_ref: Mapped[str | None] = mapped_column(String(64))
    zone_id: Mapped[str | None] = mapped_column(String(64))
    line_id: Mapped[str | None] = mapped_column(String(64))
    cooldown_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    channels: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)  # ["in_app", "email"]
    email_recipients: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Alert(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="status_valid"),
        Index("ix_alerts_rule_id_created_at", "rule_id", "created_at"),
    )

    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("edge_devices.id", ondelete="SET NULL"))
    camera_ref: Mapped[str | None] = mapped_column(String(64))
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)  # cooldown key
    context: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (CheckConstraint("status IN ('pending', 'sent', 'failed', 'skipped')", name="status_valid"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
