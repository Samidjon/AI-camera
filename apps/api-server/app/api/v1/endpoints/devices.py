"""Edge devices: registration and heartbeat (device-facing), management and configuration (users)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep, get_current_user, require_permission
from app.api.device_auth import CurrentDevice, client_ip, get_current_device
from app.core.errors import ConflictError, NotFoundError
from app.core.ratelimit import limiter
from app.models import (
    Camera,
    DeviceGroup,
    DeviceHeartbeat,
    EdgeDevice,
    EnrollmentToken,
    SynchronizationLog,
    User,
)
from app.realtime.hub import hub
from app.schemas.devices import (
    ConfigAckIn,
    ConfigDocumentOut,
    ConfigPatchIn,
    ConfigRollbackIn,
    ConfigStateOut,
    ConfigVersionOut,
    DeviceDetailOut,
    DeviceGroupIn,
    DeviceGroupOut,
    DeviceOut,
    DeviceUpdate,
    EnrollmentTokenCreated,
    EnrollmentTokenIn,
    EnrollmentTokenOut,
    HeartbeatOut,
    HeartbeatOutRow,
    RegisterIn,
    RegisterOut,
    RotateOut,
    SyncLogOut,
)
from app.services import device_config as cfg
from app.services import devices as svc

router = APIRouter()
groups_router = APIRouter()

CanRead = Annotated[User, Depends(require_permission("devices:read"))]
CanManage = Annotated[User, Depends(require_permission("devices:manage"))]


def _out(db: Session, device: EdgeDevice, detail: bool = False):
    data = DeviceOut.model_validate(device).model_dump()
    data["group_name"] = device.group.name if device.group else None
    if not detail:
        return DeviceOut(**data)
    cams = db.scalars(select(Camera).where(Camera.device_id == device.id).order_by(Camera.name))
    return DeviceDetailOut(
        **data, hardware=device.hardware, metrics=device.metrics, cameras=list(cams),
        rotate_credentials=device.rotate_credentials,
    )  # fmt: skip


# ============================================================================ device-facing


@router.post("/register", response_model=RegisterOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, request: Request, db: DbDep, settings: SettingsDep):
    """Edge agent registration with a one-time enrollment token (``Authorization: Bearer <token>``)."""
    ip = client_ip(request)
    limiter.enforce(f"register:{ip}", settings.rate_register_per_min / 60, max(1, int(settings.rate_register_per_min)))
    device, secret = svc.register_device(db, settings, request.headers.get("authorization"), body.model_dump(), ip)
    db.commit()
    hub.publish("device.status", svc.device_summary(device))
    hub.notify("info", f"Device {device.name} registered", device_id=str(device.id))
    return RegisterOut(
        device_id=device.id, device_secret=secret, heartbeat_interval_s=settings.device_heartbeat_interval_s
    )


@router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(payload: dict, request: Request, device: CurrentDevice, db: DbDep, settings: SettingsDep):
    """Device health report (identified by its credential). Reply carries the desired config version."""
    limiter.enforce(f"heartbeat:{device.id}", settings.rate_heartbeat_per_s, settings.rate_heartbeat_burst)
    reply = svc.process_heartbeat(db, settings, device, payload, client_ip(request))
    db.commit()
    return reply


@router.post("/credentials/rotate", response_model=RotateOut)
def rotate(device: CurrentDevice, db: DbDep, settings: SettingsDep):
    """Issue a new device secret. The current one stays valid until the new one is first used."""
    limiter.enforce(f"rotate:{device.id}", 1 / 60, 3)
    secret = svc.rotate_credentials(db, device)
    db.commit()
    return RotateOut(device_secret=secret)


# ============================================================================ enrollment tokens


@router.post("/enrollment-tokens", response_model=EnrollmentTokenCreated, status_code=201)
def create_token(body: EnrollmentTokenIn, user: CanManage, db: DbDep, settings: SettingsDep):
    if body.group_id and db.get(DeviceGroup, body.group_id) is None:
        raise NotFoundError("Device group not found")
    row, token = svc.create_enrollment_token(
        db, settings, user_id=user.id, name=body.name, group_id=body.group_id,
        expires_in_hours=body.expires_in_hours, max_uses=body.max_uses,
    )  # fmt: skip
    return EnrollmentTokenCreated(**EnrollmentTokenOut.model_validate(row).model_dump(), token=token)


@router.get("/enrollment-tokens", response_model=list[EnrollmentTokenOut])
def list_tokens(_: CanManage, db: DbDep):
    return list(db.scalars(select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()).limit(100)))


@router.delete("/enrollment-tokens/{token_id}", status_code=204)
def revoke_token(token_id: uuid.UUID, user: CanManage, db: DbDep):
    row = db.get(EnrollmentToken, token_id)
    if row is None:
        raise NotFoundError("Enrollment token not found")
    row.revoked_at = svc.utcnow()
    svc.audit(db, "device.enrollment_token_revoked", user_id=user.id, details={"token_id": str(token_id)})
    return Response(status_code=204)


# ============================================================================ management


@router.get("", response_model=list[DeviceOut])
def list_devices(
    _: CanRead,
    db: DbDep,
    status_: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    group_id: uuid.UUID | None = None,
):
    q = select(EdgeDevice).order_by(EdgeDevice.name)
    if status_:
        q = q.where(EdgeDevice.status == status_)
    if group_id:
        q = q.where(EdgeDevice.group_id == group_id)
    return [_out(db, d) for d in db.scalars(q)]


@router.get("/{device_id}", response_model=DeviceDetailOut)
def get_device(device_id: uuid.UUID, _: CanRead, db: DbDep):
    return _out(db, svc.get_device_or_404(db, device_id), detail=True)


@router.patch("/{device_id}", response_model=DeviceDetailOut)
def update_device(device_id: uuid.UUID, body: DeviceUpdate, user: CanManage, db: DbDep):
    device = svc.get_device_or_404(db, device_id)
    fields = body.model_dump(exclude_unset=True)
    if "group_id" in fields and fields["group_id"] and db.get(DeviceGroup, fields["group_id"]) is None:
        raise NotFoundError("Device group not found")
    for key, value in fields.items():
        if key == "name" and value is None:
            continue
        setattr(device, key, value)
    svc.audit(
        db, "device.updated", user_id=user.id, device_id=device.id, details={k: str(v) for k, v in fields.items()}
    )
    db.flush()
    db.refresh(device)
    hub.publish("device.status", svc.device_summary(device))
    return _out(db, device, detail=True)


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: uuid.UUID, user: CanManage, db: DbDep):
    """Revoke and remove the device. Its events stay (without the device link)."""
    device = svc.get_device_or_404(db, device_id)
    name = device.name
    svc.delete_device(db, device, user_id=user.id)
    db.commit()
    hub.notify("warning", f"Device {name} deleted", device_id=str(device_id))
    return Response(status_code=204)


@router.post("/{device_id}/revoke", response_model=DeviceDetailOut)
def revoke(device_id: uuid.UUID, user: CanManage, db: DbDep):
    device = svc.get_device_or_404(db, device_id)
    svc.revoke_device(db, device, user_id=user.id)
    db.commit()
    hub.publish("device.status", svc.device_summary(device))
    hub.notify("warning", f"Device {device.name} revoked", device_id=str(device.id))
    return _out(db, device, detail=True)


@router.post("/{device_id}/rotate-credentials", response_model=DeviceDetailOut)
def request_rotation(device_id: uuid.UUID, user: CanManage, db: DbDep):
    """Ask the device to rotate its credential at its next heartbeat."""
    device = svc.get_device_or_404(db, device_id)
    device.rotate_credentials = True
    svc.audit(db, "device.rotation_requested", user_id=user.id, device_id=device.id)
    return _out(db, device, detail=True)


@router.get("/{device_id}/heartbeats", response_model=list[HeartbeatOutRow])
def heartbeats(device_id: uuid.UUID, _: CanRead, db: DbDep, limit: Annotated[int, Query(ge=1, le=1000)] = 100):
    svc.get_device_or_404(db, device_id)
    return list(db.scalars(select(DeviceHeartbeat).where(DeviceHeartbeat.device_id == device_id)
                           .order_by(DeviceHeartbeat.received_at.desc()).limit(limit)))  # fmt: skip


@router.get("/{device_id}/sync-logs", response_model=list[SyncLogOut])
def sync_logs(device_id: uuid.UUID, _: CanRead, db: DbDep, limit: Annotated[int, Query(ge=1, le=500)] = 50):
    svc.get_device_or_404(db, device_id)
    return list(db.scalars(select(SynchronizationLog).where(SynchronizationLog.device_id == device_id)
                           .order_by(SynchronizationLog.id.desc()).limit(limit)))  # fmt: skip


# ============================================================================ configuration


def _config_reader(request: Request, db: DbDep, settings: SettingsDep) -> EdgeDevice | User:
    """GET configuration is used by both the device itself and dashboard users."""
    if request.headers.get("x-aci-device-id"):
        return get_current_device(request, db, settings)
    scheme, token = get_authorization_scheme_param(request.headers.get("authorization"))
    creds = HTTPAuthorizationCredentials(scheme=scheme, credentials=token) if token else None
    user = get_current_user(db, settings, creds)
    return require_permission("devices:read")(user)


@router.get("/{device_id}/configuration", response_model=ConfigDocumentOut | ConfigStateOut)
def get_configuration(device_id: uuid.UUID, db: DbDep, caller: Annotated[EdgeDevice | User, Depends(_config_reader)]):
    if isinstance(caller, EdgeDevice):
        svc.require_same_device(caller, device_id)
        return ConfigDocumentOut(**cfg.current_document(db, caller))
    device = svc.get_device_or_404(db, device_id)
    row = cfg.latest(db, device.id)
    doc = cfg.current_document(db, device)
    return ConfigStateOut(
        **doc, status=row.status if row else None, applied_version=device.applied_config_version,
        acknowledged_at=row.acknowledged_at if row else None, ack=row.ack if row else {},
    )  # fmt: skip


@router.patch("/{device_id}/configuration", response_model=ConfigStateOut)
def patch_configuration(device_id: uuid.UUID, body: ConfigPatchIn, user: CanManage, db: DbDep):
    device = svc.get_device_or_404(db, device_id)
    row, _changed = cfg.patch_configuration(
        db, device, base_version=body.base_version, patch=body.config, replace=body.replace,
        comment=body.comment, user_id=user.id,
    )  # fmt: skip
    db.commit()
    hub.publish("device.status", svc.device_summary(device))
    doc = cfg.current_document(db, device)
    return ConfigStateOut(**doc, status=row.status if row else None, applied_version=device.applied_config_version,
                          acknowledged_at=row.acknowledged_at if row else None, ack=row.ack if row else {})  # fmt: skip


@router.post("/{device_id}/configuration/rollback", response_model=ConfigStateOut)
def rollback_configuration(device_id: uuid.UUID, body: ConfigRollbackIn, user: CanManage, db: DbDep):
    device = svc.get_device_or_404(db, device_id)
    row = cfg.rollback_configuration(db, device, to_version=body.to_version, base_version=body.base_version,
                                     comment=body.comment, user_id=user.id)  # fmt: skip
    db.commit()
    hub.publish("device.status", svc.device_summary(device))
    return ConfigStateOut(**cfg.current_document(db, device), status=row.status,
                          applied_version=device.applied_config_version, acknowledged_at=None, ack={})  # fmt: skip


@router.get("/{device_id}/configuration/history", response_model=list[ConfigVersionOut])
def configuration_history(device_id: uuid.UUID, _: CanRead, db: DbDep):
    svc.get_device_or_404(db, device_id)
    return cfg.history(db, device_id)


@router.post("/{device_id}/configuration/ack", response_model=ConfigVersionOut)
def acknowledge(device_id: uuid.UUID, body: ConfigAckIn, device: CurrentDevice, db: DbDep, settings: SettingsDep):
    svc.require_same_device(device, device_id)
    row = cfg.acknowledge(db, settings, device, body.model_dump())
    db.commit()
    return row


# ============================================================================ groups


def _group_out(db: Session, g: DeviceGroup) -> DeviceGroupOut:
    count = db.scalar(select(func.count()).select_from(EdgeDevice).where(EdgeDevice.group_id == g.id)) or 0
    return DeviceGroupOut(id=g.id, name=g.name, description=g.description, device_count=count)


@groups_router.get("", response_model=list[DeviceGroupOut])
def list_groups(_: CanRead, db: DbDep):
    return [_group_out(db, g) for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]


@groups_router.post("", response_model=DeviceGroupOut, status_code=201)
def create_group(body: DeviceGroupIn, user: CanManage, db: DbDep):
    if db.scalar(select(DeviceGroup.id).where(DeviceGroup.name == body.name.strip())):
        raise ConflictError(f"Group '{body.name}' already exists")
    g = DeviceGroup(name=body.name.strip(), description=body.description)
    db.add(g)
    db.flush()
    svc.audit(db, "device_group.created", user_id=user.id, details={"group_id": str(g.id), "name": g.name})
    return _group_out(db, g)


@groups_router.patch("/{group_id}", response_model=DeviceGroupOut)
def update_group(group_id: uuid.UUID, body: DeviceGroupIn, user: CanManage, db: DbDep):
    g = db.get(DeviceGroup, group_id)
    if g is None:
        raise NotFoundError("Device group not found")
    g.name, g.description = body.name.strip(), body.description
    svc.audit(db, "device_group.updated", user_id=user.id, details={"group_id": str(g.id)})
    return _group_out(db, g)


@groups_router.delete("/{group_id}", status_code=204)
def delete_group(group_id: uuid.UUID, user: CanManage, db: DbDep):
    g = db.get(DeviceGroup, group_id)
    if g is None:
        raise NotFoundError("Device group not found")
    db.delete(g)  # devices keep running; their group_id becomes NULL
    svc.audit(db, "device_group.deleted", user_id=user.id, details={"group_id": str(group_id)})
    return Response(status_code=204)
