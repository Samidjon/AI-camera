"""System settings (retention, privacy) and the read-only server configuration."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.api.deps import CurrentUser, DbDep, SettingsDep, require_permission
from app.models import AuditLog, User
from app.services import system_settings
from app.services.system_settings import SystemSettingsDoc

router = APIRouter()
CanManage = Annotated[User, Depends(require_permission("settings:manage"))]


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events_retention_days: int | None = Field(default=None, ge=0, le=3650)
    observations_retention_days: int | None = Field(default=None, ge=0, le=3650)
    store_person_attributes: bool | None = None
    show_person_attributes: bool | None = None


@router.get("/settings", response_model=SystemSettingsDoc)
def get_settings_doc(_: CurrentUser, db: DbDep):
    return system_settings.load(db)


@router.patch("/settings", response_model=SystemSettingsDoc)
def patch_settings(body: SettingsPatch, user: CanManage, db: DbDep):
    changes = body.model_dump(exclude_none=True)
    doc = system_settings.save(db, changes, user.id)
    db.add(AuditLog(user_id=user.id, action="settings.changed", entity_type="system_settings",
                    entity_id=system_settings.KEY, details=changes))  # fmt: skip
    return doc


@router.get("/configuration")
def configuration(_: CurrentUser, settings: SettingsDep) -> dict[str, Any]:
    """Effective server configuration (environment), without secrets."""
    return {
        "version": __version__,
        "environment": settings.environment,
        "database": settings.database_url.split("://", 1)[0] if settings.database_url else None,
        "redis_configured": settings.redis_configured,
        "access_token_minutes": settings.access_token_expire_minutes,
        "cors_origins": settings.cors_origins,
        "devices": {
            "heartbeat_interval_s": settings.device_heartbeat_interval_s,
            "offline_after_s": f"max({settings.device_offline_min_s}, {settings.device_offline_factor} × interval)",
            "credential_max_age_days": settings.device_credential_max_age_days,
            "credential_grace_s": settings.device_credential_grace_s,
            "enrollment_token_ttl_hours": settings.enrollment_token_ttl_hours,
            "heartbeat_retention_days": settings.heartbeat_retention_days,
        },
        "events": {
            "batch_max": settings.events_batch_max,
            "max_future_skew_s": settings.events_max_future_skew_s,
            "max_age_days": settings.events_max_age_days,
        },
        "rate_limits": {
            "events_per_s": settings.rate_events_per_s,
            "events_burst": settings.rate_events_burst,
            "heartbeat_per_s": settings.rate_heartbeat_per_s,
            "register_per_min": settings.rate_register_per_min,
        },
    }
