"""Edge device lifecycle: enrollment, registration, authentication, heartbeat, status, revocation,
credential rotation.

Secrets (enrollment tokens, device secrets) are 256-bit random values; only their SHA-256 hashes are
stored. A device authenticates with ``Authorization: Bearer <secret>`` + ``X-ACI-Device-ID``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from app.models import (
    AuditLog,
    Camera,
    DeviceConfiguration,
    DeviceCredential,
    DeviceHeartbeat,
    DeviceStatus,
    EdgeDevice,
    EnrollmentToken,
    SynchronizationLog,
)
from app.models.camera import CameraSourceType, CameraStatus
from app.realtime.hub import hub

logger = logging.getLogger("aci.devices")

CAMERA_STATUSES = {s.value for s in CameraStatus}
SOURCE_TYPES = {s.value for s in CameraSourceType}


class DeviceAuthError(AuthenticationError):
    code = "invalid_device_credentials"


class DeviceRevokedError(AuthenticationError):
    code = "device_revoked"


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone-aware columns; they are UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def audit(db: Session, action: str, *, user_id=None, device_id=None, details: dict | None = None, ip=None) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type="edge_device" if device_id else None,
            entity_id=str(device_id) if device_id else None,
            details=details or {},
            ip_address=ip,
        )
    )


def device_summary(d: EdgeDevice) -> dict:
    """Compact form used in real-time messages."""
    return {
        "id": str(d.id),
        "name": d.name,
        "status": d.status,
        "status_reason": d.status_reason,
        "last_heartbeat_at": aware(d.last_heartbeat_at).isoformat() if d.last_heartbeat_at else None,
        "agent_version": d.agent_version,
        "ai_status": d.ai_status,
        "camera_status": d.camera_status,
        "sync_status": d.sync_status,
        "applied_config_version": d.applied_config_version,
        "desired_config_version": d.desired_config_version,
    }


# ---------------------------------------------------------------------------- enrollment


def create_enrollment_token(
    db: Session, settings: Settings, *, user_id, name: str | None, group_id, expires_in_hours: int | None, max_uses: int
) -> tuple[EnrollmentToken, str]:
    token = new_secret("aci_enr")
    row = EnrollmentToken(
        name=name,
        token_hash=hash_secret(token),
        group_id=group_id,
        created_by=user_id,
        expires_at=utcnow() + timedelta(hours=expires_in_hours or settings.enrollment_token_ttl_hours),
        max_uses=max_uses,
        uses=0,
    )
    db.add(row)
    db.flush()
    audit(db, "device.enrollment_token_created", user_id=user_id, details={"token_id": str(row.id), "name": name})
    return row, token


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise DeviceAuthError("Missing bearer credential")
    value = authorization[7:].strip()
    if not 16 <= len(value) <= 256:
        raise DeviceAuthError("Malformed credential")
    return value


def register_device(
    db: Session, settings: Settings, authorization: str | None, payload: dict, ip: str | None
) -> tuple[EdgeDevice, str]:
    token_value = _bearer(authorization)
    query = select(EnrollmentToken).where(EnrollmentToken.token_hash == hash_secret(token_value))
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()  # two devices racing for the last use of a token
    token = db.scalar(query)
    now = utcnow()
    if token is None or token.revoked_at is not None or aware(token.expires_at) < now or token.uses >= token.max_uses:
        db.add(
            SynchronizationLog(
                kind="registration", status="failed", details={"reason": "invalid enrollment token", "ip": ip}
            )
        )
        db.commit()
        raise AuthenticationError("Invalid or expired enrollment token", code="invalid_enrollment_token")
    token.uses += 1
    device_id: uuid.UUID = payload["device_id"]
    device = db.get(EdgeDevice, device_id)
    re_registration = device is not None
    if device is None:
        device = EdgeDevice(
            id=device_id,
            name=payload["device_name"],
            group_id=token.group_id,
            status=DeviceStatus.PENDING.value,
            hardware=payload.get("hardware") or {},
            metrics={},
            ai_status={},
            camera_status={},
            sync_status={},
            heartbeat_interval_s=settings.device_heartbeat_interval_s,
            desired_config_version=0,
            applied_config_version=0,
            rotate_credentials=False,
        )
        db.add(device)
    else:
        # Re-registration (agent reinstalled, or revoked device enrolled again with a new token):
        # every previous credential becomes invalid.
        db.execute(
            update(DeviceCredential)
            .where(DeviceCredential.device_id == device.id, DeviceCredential.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        device.revoked_at = None
        device.status = DeviceStatus.PENDING.value
        device.hardware = payload.get("hardware") or device.hardware
        device.rotate_credentials = False
    device.agent_version = payload.get("agent_version")
    device.registered_at = now
    device.status_reason = "registered — waiting for the first heartbeat"
    device.status_changed_at = now
    device.last_seen_ip = ip
    secret = new_secret("aci_dev")
    db.flush()
    db.add(DeviceCredential(device_id=device.id, secret_hash=hash_secret(secret), origin="enrollment"))
    db.add(SynchronizationLog(device_id=device.id, kind="registration", status="ok",
                              details={"re_registration": re_registration, "token_id": str(token.id)}))  # fmt: skip
    audit(db, "device.re_registered" if re_registration else "device.registered", device_id=device.id, ip=ip,
          details={"token_id": str(token.id), "agent_version": device.agent_version})  # fmt: skip
    logger.info("Device %s", "re-registered" if re_registration else "registered", extra={"device_id": str(device.id)})
    return device, secret


# ---------------------------------------------------------------------------- authentication


def authenticate_device(
    db: Session, settings: Settings, authorization: str | None, device_header: str | None
) -> EdgeDevice:
    secret = _bearer(authorization)
    try:
        device_id = uuid.UUID(device_header or "")
    except ValueError:
        raise DeviceAuthError("Missing or invalid X-ACI-Device-ID") from None
    cred = db.scalar(select(DeviceCredential).where(DeviceCredential.secret_hash == hash_secret(secret)))
    now = utcnow()
    if cred is None or not hmac.compare_digest(str(cred.device_id), str(device_id)):
        raise DeviceAuthError("Invalid device credentials")
    if cred.revoked_at is not None or (cred.expires_at is not None and aware(cred.expires_at) < now):
        raise DeviceRevokedError("Device credential revoked or expired")
    device = db.get(EdgeDevice, device_id)
    if device is None or device.revoked_at is not None:
        raise DeviceRevokedError("Device revoked")
    if cred.origin == "rotation" and cred.last_used_at is None:
        # First use of a rotated credential: the device has it, so older ones may now expire.
        db.execute(
            update(DeviceCredential)
            .where(
                DeviceCredential.device_id == device.id,
                DeviceCredential.id != cred.id,
                DeviceCredential.revoked_at.is_(None),
                DeviceCredential.expires_at.is_(None),
            )
            .values(expires_at=now + timedelta(seconds=settings.device_credential_grace_s))
        )
        audit(db, "device.credentials_rotated", device_id=device.id)
    last = aware(cred.last_used_at)
    if last is None or now - last > timedelta(seconds=60):
        cred.last_used_at = now
    return device


def rotate_credentials(db: Session, device: EdgeDevice) -> str:
    """Issue a new credential. Older ones stay valid until the new one is first used (+ grace)."""
    secret = new_secret("aci_dev")
    db.add(DeviceCredential(device_id=device.id, secret_hash=hash_secret(secret), origin="rotation"))
    device.rotate_credentials = False
    db.add(SynchronizationLog(device_id=device.id, kind="credentials", status="ok", details={"action": "rotated"}))
    return secret


def credential_rotation_due(db: Session, settings: Settings, device: EdgeDevice) -> bool:
    if device.rotate_credentials:
        return True
    newest = db.scalar(
        select(DeviceCredential.created_at)
        .where(DeviceCredential.device_id == device.id, DeviceCredential.revoked_at.is_(None))
        .order_by(DeviceCredential.created_at.desc())
        .limit(1)
    )
    return newest is not None and utcnow() - aware(newest) > timedelta(days=settings.device_credential_max_age_days)


def revoke_device(db: Session, device: EdgeDevice, *, user_id=None, reason: str = "revoked by administrator") -> None:
    now = utcnow()
    device.revoked_at = now
    db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id == device.id, DeviceCredential.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    set_status(device, DeviceStatus.REVOKED.value, reason)
    audit(db, "device.revoked", user_id=user_id, device_id=device.id, details={"reason": reason})


def delete_device(db: Session, device: EdgeDevice, *, user_id=None) -> None:
    revoke_device(db, device, user_id=user_id, reason="deleted")
    audit(db, "device.deleted", user_id=user_id, device_id=device.id, details={"name": device.name})
    db.delete(device)  # events keep their data (device_id → NULL, camera_ref kept)


def get_device_or_404(db: Session, device_id: uuid.UUID) -> EdgeDevice:
    device = db.get(EdgeDevice, device_id)
    if device is None:
        raise NotFoundError("Device not found")
    return device


# ---------------------------------------------------------------------------- status


def offline_after(settings: Settings, device: EdgeDevice) -> timedelta:
    return timedelta(
        seconds=max(settings.device_offline_min_s, settings.device_offline_factor * device.heartbeat_interval_s)
    )


def set_status(device: EdgeDevice, status: str, reason: str | None) -> bool:
    changed = device.status != status
    if changed:
        device.status_changed_at = utcnow()
    device.status = status
    device.status_reason = (reason or None) and reason[:300]
    return changed


def derive_status(
    db: Session, settings: Settings, device: EdgeDevice, now: datetime | None = None
) -> tuple[str, str | None]:
    """Server-side health classification from the latest heartbeat."""
    now = now or utcnow()
    if device.revoked_at is not None:
        return DeviceStatus.REVOKED.value, "revoked"
    last = aware(device.last_heartbeat_at)
    if last is None:
        return DeviceStatus.PENDING.value, "registered — waiting for the first heartbeat"
    age = now - last
    if age > offline_after(settings, device):
        return DeviceStatus.OFFLINE.value, f"no heartbeat for {int(age.total_seconds())} s"
    m = device.metrics or {}
    health = m.get("health") or {}
    if health.get("database_ok") is False:
        return DeviceStatus.ERROR.value, "local database integrity check failed"
    latest = db.scalar(
        select(DeviceConfiguration)
        .where(DeviceConfiguration.device_id == device.id)
        .order_by(DeviceConfiguration.version.desc())
        .limit(1)
    )
    problems: list[str] = []
    if latest is not None and latest.status == "failed":
        problems.append(f"configuration v{latest.version} failed to apply")
    by_status = (device.camera_status or {}).get("by_status") or {}
    bad = int(by_status.get("error", 0)) + int(by_status.get("reconnecting", 0))
    if bad:
        problems.append(f"{bad} camera(s) failing")
    ai = device.ai_status or {}
    if ai.get("enabled") and ai.get("mode") in ("degraded", "error", "recovering", "offline"):
        problems.append(f"AI {ai.get('mode')}")
    if health.get("storage_paused"):
        problems.append("event storage paused (disk almost full)")
    hw = m.get("hardware") or {}
    disk = (hw.get("disk") or {}).get("used_percent")
    if isinstance(disk, int | float) and disk >= 95:
        problems.append(f"disk {disk:.0f}% full")
    temp = (hw.get("temperature") or {}).get("max_c")
    if isinstance(temp, int | float) and temp >= 85:
        problems.append(f"temperature {temp:.0f} °C")
    if problems:
        return DeviceStatus.DEGRADED.value, "; ".join(problems)
    pending = int((device.sync_status or {}).get("pending") or 0)
    if pending > 0:
        return DeviceStatus.SYNCHRONIZING.value, f"{pending} events waiting for upload"
    return DeviceStatus.ONLINE.value, None


def refresh_status(db: Session, settings: Settings, device: EdgeDevice, *, notify: bool = True) -> bool:
    previous = device.status
    status, reason = derive_status(db, settings, device)
    changed = set_status(device, status, reason)
    hub.publish("device.status", device_summary(device))
    if changed and notify:
        level = {"offline": "warning", "error": "error", "degraded": "warning", "revoked": "warning"}.get(
            status, "info"
        )
        hub.notify(level, f"Device {device.name}: {previous} → {status}", device_id=str(device.id), reason=reason)
    return changed


# ---------------------------------------------------------------------------- heartbeat


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _sync_cameras(db: Session, device: EdgeDevice, reported: list[dict]) -> None:
    existing = {c.external_id: c for c in db.scalars(select(Camera).where(Camera.device_id == device.id))}
    seen = set()
    for item in reported[:256]:
        ext = str(item.get("id") or "")[:64]
        if not ext or ext in seen:
            continue
        seen.add(ext)
        status = item.get("status") if item.get("status") in CAMERA_STATUSES else "offline"
        cam = existing.get(ext)
        old_status = cam.status if cam is not None else None  # None = newly reported camera
        if cam is None:
            cam = Camera(device_id=device.id, external_id=ext, name="", source_type="ffmpeg", status=status)
            db.add(cam)
        cam.name = str(item.get("name") or ext)[:120]
        cam.location = str(item["location"])[:255] if item.get("location") else None
        cam.source_type = item.get("source_type") if item.get("source_type") in SOURCE_TYPES else "ffmpeg"
        cam.status = status
        cam.is_enabled = bool(item.get("is_enabled", True))
        cam.ai_enabled = item.get("ai_enabled") if isinstance(item.get("ai_enabled"), bool) else None
        fps = item.get("fps")
        cam.fps = float(fps) if isinstance(fps, int | float) and fps >= 0 else None
        w, h = item.get("width"), item.get("height")
        cam.resolution_width = w if isinstance(w, int) and w > 0 else None
        cam.resolution_height = h if isinstance(h, int) and h > 0 else None
        cam.last_error = str(item["last_error"])[:300] if item.get("last_error") else None
        if status == "online":
            cam.last_frame_at = utcnow()
        if old_status != status:
            hub.publish(
                "camera.status",
                {"device_id": str(device.id), "camera_id": ext, "name": cam.name, "status": status,
                 "previous": old_status, "error": cam.last_error},
            )  # fmt: skip
    gone = [c.id for ext, c in existing.items() if ext not in seen]
    if gone:
        db.execute(delete(Camera).where(Camera.id.in_(gone)))


def process_heartbeat(db: Session, settings: Settings, device: EdgeDevice, payload: dict, ip: str | None) -> dict:
    now = utcnow()
    device.last_heartbeat_at = now
    device.last_seen_ip = ip
    device.agent_version = str(payload.get("agent_version") or device.agent_version or "")[:40] or None
    if isinstance(payload.get("hardware"), dict):
        device.hardware = payload["hardware"]
    device.metrics = {
        "hardware": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        "health": payload.get("health") if isinstance(payload.get("health"), dict) else {},
        "vision": payload.get("vision") if isinstance(payload.get("vision"), dict) else {},
        "events": payload.get("events") if isinstance(payload.get("events"), dict) else {},
        "uptime_s": payload.get("uptime_s"),
        "reported_name": str(payload.get("device_name") or "")[:120],
        "link_state": payload.get("link_state"),
    }
    device.ai_status = payload.get("ai") if isinstance(payload.get("ai"), dict) else {}
    cams = payload.get("cameras") if isinstance(payload.get("cameras"), dict) else {}
    device.camera_status = {"total": _int(cams.get("total")), "by_status": cams.get("by_status") or {}}
    sync = payload.get("sync") if isinstance(payload.get("sync"), dict) else {}
    device.sync_status = {k: sync.get(k) for k in ("pending", "oldest_pending", "dropped", "max", "enabled")}
    interval = _int(payload.get("heartbeat_interval_s"), device.heartbeat_interval_s)
    device.heartbeat_interval_s = max(5, min(3600, interval))
    reported_cfg = _int(payload.get("config_version"), device.applied_config_version)
    if reported_cfg > device.applied_config_version:
        device.applied_config_version = reported_cfg
    if isinstance(payload.get("camera_list"), list):
        _sync_cameras(db, device, payload["camera_list"])
    refresh_status(db, settings, device)
    by_status = device.camera_status.get("by_status") or {}
    db.add(
        DeviceHeartbeat(
            device_id=device.id,
            received_at=now,
            status=device.status,
            agent_version=device.agent_version,
            cameras_online=_int(by_status.get("online")),
            cameras_total=device.camera_status.get("total", 0),
            ai_mode=str(device.ai_status.get("mode") or "")[:20] or None,
            queue_pending=_int(device.sync_status.get("pending")),
            metrics={k: (device.metrics["hardware"] or {}).get(k) for k in ("cpu", "memory", "temperature", "gpu")},
        )
    )
    return {
        "config_version": device.desired_config_version,
        "heartbeat_interval_s": settings.device_heartbeat_interval_s,
        "rotate_credentials": credential_rotation_due(db, settings, device),
        "server_time": now.isoformat(),
        "status": device.status,
    }


def mark_stale_devices(db: Session, settings: Settings) -> int:
    """Background check: devices whose heartbeats stopped become offline (with a notification)."""
    now = utcnow()
    changed = 0
    candidates = db.scalars(
        select(EdgeDevice).where(
            EdgeDevice.revoked_at.is_(None),
            EdgeDevice.status != DeviceStatus.OFFLINE.value,
            EdgeDevice.last_heartbeat_at.is_not(None),
        )  # fmt: skip
    )
    for device in candidates:
        if now - aware(device.last_heartbeat_at) > offline_after(settings, device):
            changed += int(refresh_status(db, settings, device))
            logger.warning("Device went offline", extra={"device_id": str(device.id), "device_name": device.name})
    return changed


def prune_heartbeats(db: Session, settings: Settings) -> int:
    cutoff = utcnow() - timedelta(days=settings.heartbeat_retention_days)
    return db.execute(delete(DeviceHeartbeat).where(DeviceHeartbeat.received_at < cutoff)).rowcount or 0


def require_same_device(device: EdgeDevice, device_id: uuid.UUID) -> None:
    if device.id != device_id:
        raise PermissionDeniedError("A device may only access its own resources")
