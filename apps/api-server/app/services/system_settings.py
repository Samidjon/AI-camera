"""Runtime system settings (retention, privacy) stored in ``system_settings``, defaults in code."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete
from sqlalchemy.orm import Session

from app.models import CameraEvent
from app.models.settings import SystemSetting
from app.processing.event_processor import PERSON_EVENT_TYPES

logger = logging.getLogger("aci.settings")
KEY = "general"


class SystemSettingsDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Data retention on the central server (edge devices have their own, see device configuration).
    events_retention_days: int = Field(default=365, ge=0, le=3650, description="0 = keep forever")
    observations_retention_days: int = Field(
        default=30, ge=0, le=3650, description="Person (AI) events; 0 = same as events"
    )
    # Privacy
    store_person_attributes: bool = Field(
        default=True, description="Keep AI clothing attributes of person events (otherwise removed on arrival)"
    )
    show_person_attributes: bool = Field(default=True, description="Show clothing attributes in the dashboard")


def load(db: Session) -> SystemSettingsDoc:
    row = db.get(SystemSetting, KEY)
    try:
        return SystemSettingsDoc.model_validate(row.value if row else {})
    except Exception:  # noqa: BLE001 - a broken row must not break ingestion
        logger.error("Invalid system settings row — using defaults")
        return SystemSettingsDoc()


def save(db: Session, changes: dict, user_id) -> SystemSettingsDoc:
    current = load(db).model_dump()
    doc = SystemSettingsDoc.model_validate(current | changes)
    row = db.get(SystemSetting, KEY)
    if row is None:
        db.add(SystemSetting(key=KEY, value=doc.model_dump(), updated_by=user_id))
    else:
        row.value = doc.model_dump()
        row.updated_by = user_id
    return doc


def apply_retention(db: Session, now: datetime | None = None) -> dict:
    """Delete events older than the configured retention. Hourly aggregates are kept (no personal data)."""
    now = now or datetime.now(UTC)
    s = load(db)
    report = {"events": 0, "observations": 0}
    if s.observations_retention_days:
        cutoff = now - timedelta(days=s.observations_retention_days)
        report["observations"] = (
            db.execute(
                delete(CameraEvent)
                .where(and_(CameraEvent.event_type.in_(PERSON_EVENT_TYPES), CameraEvent.occurred_at < cutoff))
                .execution_options(synchronize_session=False)
            ).rowcount
            or 0
        )
    if s.events_retention_days:
        cutoff = now - timedelta(days=s.events_retention_days)
        report["events"] = (
            db.execute(
                delete(CameraEvent).where(CameraEvent.occurred_at < cutoff).execution_options(synchronize_session=False)
            ).rowcount
            or 0
        )
    if any(report.values()):
        logger.info("Retention applied", extra=report)
    return report
