"""Versioned edge-device configuration.

* Every change creates a new immutable version (``device_configurations``); versions are numbered per
  device. A change must name the version it was based on (``base_version``) — if someone else changed
  the configuration in the meantime the request fails with 409 (optimistic concurrency).
* Changes are JSON merge patches (RFC 7386: ``null`` removes a key) validated against
  :class:`DeviceConfigDoc`; unknown keys are rejected.
* A rollback creates a new version with the content of an older one (history is never rewritten).
* The device downloads the newest version (with a SHA-256 checksum it verifies), applies it and
  acknowledges: ``applied`` / ``partial`` / ``failed`` with per-key details.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import ConfigStatus, DeviceConfiguration, EdgeDevice, SynchronizationLog
from app.realtime.hub import hub
from app.services.devices import audit, refresh_status, utcnow

_FORBID = ConfigDict(extra="forbid")
Point = Annotated[float, Field(ge=0.0, le=1.0)]


class MotionSettings(BaseModel):
    model_config = _FORBID
    enabled: bool | None = None
    algorithm: Literal["frame_diff", "background", "mog2", "knn"] | None = None
    sensitivity: int | None = Field(default=None, ge=1, le=100)
    min_object_area: float | None = Field(default=None, ge=0.0001, le=0.5)
    process_fps: float | None = Field(default=None, ge=0.5, le=30)
    motion_end_delay_s: float | None = Field(default=None, ge=0.2, le=60)
    restrict_to_zones: bool | None = None
    activity_interval_s: int | None = Field(default=None, ge=10, le=3600)


class ZoneSettings(BaseModel):
    model_config = _FORBID
    name: str = Field(min_length=1, max_length=80)
    role: Literal["monitor", "exclude"] = "monitor"
    polygon: list[tuple[Point, Point]] = Field(min_length=3, max_length=64)
    is_enabled: bool = True


class CameraSettings(BaseModel):
    model_config = _FORBID
    is_enabled: bool | None = None
    max_fps: float | None = Field(default=None, ge=0.5, le=60)
    ai_enabled: bool | None = None
    motion: MotionSettings | None = None
    # When present, replaces the camera's detection zones (managed centrally).
    zones: list[ZoneSettings] | None = Field(default=None, max_length=32)

    @field_validator("zones")
    @classmethod
    def _unique_names(cls, v):
        if v is not None and len({z.name for z in v}) != len(v):
            raise ValueError("zone names must be unique per camera")
        return v


class EventSettings(BaseModel):
    model_config = _FORBID
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    max_db_mb: int | None = Field(default=None, ge=16, le=1_048_576)
    min_free_disk_mb: int | None = Field(default=None, ge=50, le=1_048_576)


class DeviceConfigDoc(BaseModel):
    model_config = _FORBID
    device_name: str | None = Field(default=None, min_length=1, max_length=120)
    heartbeat_interval_s: int | None = Field(default=None, ge=5, le=3600)
    ai_enabled: bool | None = None
    events: EventSettings | None = None
    cameras: dict[Annotated[str, Field(min_length=1, max_length=64)], CameraSettings] | None = Field(
        default=None, max_length=256
    )


def canonical_json(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(doc: dict) -> str:
    return hashlib.sha256(canonical_json(doc).encode()).hexdigest()


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 JSON merge patch."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def validate_config(doc: dict) -> dict:
    try:
        model = DeviceConfigDoc.model_validate(doc)
    except ValidationError as exc:
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()[:20]]
        raise AppError("Invalid device configuration", code="invalid_configuration", details=details) from None
    return model.model_dump(mode="json", exclude_none=True)


def latest(db: Session, device_id: uuid.UUID) -> DeviceConfiguration | None:
    return db.scalar(
        select(DeviceConfiguration)
        .where(DeviceConfiguration.device_id == device_id)
        .order_by(DeviceConfiguration.version.desc())
        .limit(1)
    )


def current_document(db: Session, device: EdgeDevice) -> dict:
    row = latest(db, device.id)
    config = row.config if row else {}
    return {"version": row.version if row else 0, "config": config, "checksum": checksum(config)}


def _new_version(
    db: Session, device: EdgeDevice, config: dict, *, user_id, comment: str | None, rollback_of: int | None
) -> DeviceConfiguration:
    prev = latest(db, device.id)
    version = (prev.version if prev else 0) + 1
    db.execute(
        update(DeviceConfiguration)
        .where(DeviceConfiguration.device_id == device.id, DeviceConfiguration.status == ConfigStatus.PENDING.value)
        .values(status=ConfigStatus.SUPERSEDED.value)
    )
    row = DeviceConfiguration(
        device_id=device.id,
        version=version,
        config=config,
        checksum=checksum(config),
        comment=(comment or None) and comment[:300],
        rollback_of=rollback_of,
        created_by=user_id,
        status=ConfigStatus.PENDING.value,
        ack={},
    )
    db.add(row)
    device.desired_config_version = version
    db.flush()
    audit(db, "device.configuration_rolled_back" if rollback_of else "device.configuration_changed",
          user_id=user_id, device_id=device.id, details={"version": version, "rollback_of": rollback_of,
                                                          "comment": row.comment})  # fmt: skip
    hub.notify("info", f"Configuration v{version} queued for {device.name}", device_id=str(device.id))
    return row


def _check_base(db: Session, device: EdgeDevice, base_version: int) -> DeviceConfiguration | None:
    prev = latest(db, device.id)
    current = prev.version if prev else 0
    if base_version != current:
        raise ConflictError(
            f"Configuration changed meanwhile (current version {current}, yours {base_version}) — reload and retry",
            code="configuration_conflict",
            details={"current_version": current},
        )
    return prev


def patch_configuration(
    db: Session, device: EdgeDevice, *, base_version: int, patch: dict, replace: bool, comment: str | None, user_id
) -> tuple[DeviceConfiguration | None, bool]:
    if device.revoked_at is not None:
        raise ConflictError("Device is revoked", code="device_revoked")
    prev = _check_base(db, device, base_version)
    current = prev.config if prev else {}
    merged = validate_config(patch if replace else merge_patch(current, patch))
    if prev is not None and canonical_json(merged) == canonical_json(current):
        return prev, False
    if prev is None and not merged:
        return None, False
    return _new_version(db, device, merged, user_id=user_id, comment=comment, rollback_of=None), True


def rollback_configuration(
    db: Session, device: EdgeDevice, *, to_version: int, base_version: int, comment: str | None, user_id
) -> DeviceConfiguration:
    _check_base(db, device, base_version)
    target = db.scalar(
        select(DeviceConfiguration).where(
            DeviceConfiguration.device_id == device.id, DeviceConfiguration.version == to_version
        )
    )
    if target is None:
        raise NotFoundError(f"Configuration version {to_version} not found")
    return _new_version(db, device, copy.deepcopy(target.config), user_id=user_id,
                        comment=comment or f"rollback to v{to_version}", rollback_of=to_version)  # fmt: skip


def acknowledge(db: Session, settings, device: EdgeDevice, ack: dict) -> DeviceConfiguration:
    version = int(ack["version"])
    row = db.scalar(
        select(DeviceConfiguration).where(
            DeviceConfiguration.device_id == device.id, DeviceConfiguration.version == version
        )
    )
    if row is None:
        raise NotFoundError(f"Configuration version {version} not found")
    status = ack["status"]
    row.ack = {k: ack.get(k) for k in ("applied", "ignored", "errors")}
    row.acknowledged_at = utcnow()
    if row.status != ConfigStatus.SUPERSEDED.value or status == "applied":
        row.status = status
    if status in ("applied", "partial") and version > device.applied_config_version:
        device.applied_config_version = version
    db.add(SynchronizationLog(device_id=device.id, kind="configuration", status="ok" if status == "applied" else status,
                              details={"version": version, **row.ack}))  # fmt: skip
    audit(db, "device.configuration_acknowledged", device_id=device.id, details={"version": version, "status": status})
    if status != "applied":
        hub.notify(
            "warning" if status == "partial" else "error",
            f"{device.name}: configuration v{version} {status}",
            device_id=str(device.id),
            errors=row.ack.get("errors"),
        )
    hub.publish(
        "sync.status", {"device_id": str(device.id), "kind": "configuration", "version": version, "status": status}
    )
    refresh_status(db, settings, device, notify=False)
    return row


def history(db: Session, device_id: uuid.UUID, limit: int = 50) -> list[DeviceConfiguration]:
    return list(
        db.scalars(
            select(DeviceConfiguration)
            .where(DeviceConfiguration.device_id == device_id)
            .order_by(DeviceConfiguration.version.desc())
            .limit(limit)
        )
    )
