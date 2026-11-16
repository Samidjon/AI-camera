"""User management, roles and the permission catalog (permission ``users:manage``)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbDep, require_permission
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.permissions import PERMISSIONS
from app.core.security import hash_password
from app.models import AuditLog, Role, User
from app.schemas.auth import LoginEmail
from app.schemas.devices import UTCDateTime
from app.services.auth import create_user

router = APIRouter()
roles_router = APIRouter()
CanManage = Annotated[User, Depends(require_permission("users:manage"))]


class RoleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool
    user_count: int = 0
    editable: bool = True


class UserItem(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    last_login_at: UTCDateTime | None
    created_at: UTCDateTime


class UserCreate(BaseModel):
    email: LoginEmail
    full_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    role: str = Field(min_length=1, max_length=50)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)


class RoleUpdate(BaseModel):
    permissions: list[str] = Field(max_length=50)


def _item(u: User) -> UserItem:
    return UserItem(id=u.id, email=u.email, full_name=u.full_name, role=u.role.name, is_active=u.is_active,
                    last_login_at=u.last_login_at, created_at=u.created_at)  # fmt: skip


def _role(db: Session, name: str) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        raise NotFoundError(f"Role '{name}' not found")
    return role


def _active_admins(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .join(Role)
            .where(Role.name == "administrator", User.is_active.is_(True))
        )
        or 0
    )


@router.get("", response_model=list[UserItem])
def list_users(_: CanManage, db: DbDep):
    return [_item(u) for u in db.scalars(select(User).order_by(User.email))]


@router.post("", response_model=UserItem, status_code=201)
def create(body: UserCreate, admin: CanManage, db: DbDep):
    user = create_user(db, email=body.email, password=body.password, role_name=body.role, full_name=body.full_name)
    db.add(AuditLog(user_id=admin.id, action="user.created_by_admin", entity_type="user", entity_id=str(user.id),
                    details={"role": body.role}))  # fmt: skip
    return _item(user)


@router.patch("/{user_id}", response_model=UserItem)
def update(user_id: uuid.UUID, body: UserUpdate, admin: CanManage, db: DbDep):
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    fields = body.model_dump(exclude_unset=True)
    demoting = "role" in fields and fields["role"] != "administrator" and user.role.name == "administrator"
    disabling = fields.get("is_active") is False and user.role.name == "administrator" and user.is_active
    if (demoting or disabling) and _active_admins(db) <= 1:
        raise ConflictError("At least one active administrator must remain", code="last_administrator")
    if user.id == admin.id and (fields.get("is_active") is False or demoting):
        raise AppError("You can't disable or demote your own account", code="self_lockout")
    if "full_name" in fields:
        user.full_name = fields["full_name"]
    if fields.get("role"):
        user.role = _role(db, fields["role"])
    if "is_active" in fields and fields["is_active"] is not None:
        user.is_active = fields["is_active"]
    if fields.get("password"):
        user.password_hash = hash_password(fields["password"])
    changed = sorted(k for k in fields if k != "password") + (["password"] if fields.get("password") else [])
    db.add(AuditLog(user_id=admin.id, action="user.updated", entity_type="user", entity_id=str(user.id),
                    details={"fields": changed}))  # fmt: skip
    db.flush()
    return _item(user)


@router.delete("/{user_id}", status_code=204)
def delete(user_id: uuid.UUID, admin: CanManage, db: DbDep):
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.id == admin.id:
        raise AppError("You can't delete your own account", code="self_lockout")
    if user.role.name == "administrator" and user.is_active and _active_admins(db) <= 1:
        raise ConflictError("At least one active administrator must remain", code="last_administrator")
    db.add(AuditLog(user_id=admin.id, action="user.deleted", entity_type="user", entity_id=str(user.id),
                    details={"email": user.email}))  # fmt: skip
    db.delete(user)  # audit history keeps the row (user_id → NULL)
    return Response(status_code=204)


# ---------------------------------------------------------------------------- roles


@roles_router.get("/permissions")
def permission_catalog(_: CanManage) -> dict[str, str]:
    return PERMISSIONS


@roles_router.get("", response_model=list[RoleInfo])
def list_roles(_: CanManage, db: DbDep):
    counts = dict(db.execute(select(User.role_id, func.count()).group_by(User.role_id)).all())
    return [
        RoleInfo(
            name=r.name,
            description=r.description,
            permissions=list(r.permissions or []),
            is_system=r.is_system,
            user_count=counts.get(r.id, 0),
            editable=r.name != "administrator",
        )  # fmt: skip
        for r in db.scalars(select(Role).order_by(Role.name))
    ]


@roles_router.put("/{name}", response_model=RoleInfo)
def update_role(name: str, body: RoleUpdate, admin: CanManage, db: DbDep):
    role = _role(db, name)
    if role.name == "administrator":
        raise AppError("The administrator role always has every permission", code="role_locked")
    unknown = sorted(set(body.permissions) - set(PERMISSIONS))
    if unknown:
        raise AppError(f"Unknown permissions: {', '.join(unknown)}", code="unknown_permission")
    if "users:manage" in body.permissions:
        raise AppError("users:manage is reserved for administrators", code="role_locked")
    before = list(role.permissions or [])
    role.permissions = sorted(set(body.permissions))
    db.add(AuditLog(user_id=admin.id, action="role.permissions_changed", entity_type="role", entity_id=role.name,
                    details={"before": before, "after": role.permissions}))  # fmt: skip
    return RoleInfo(name=role.name, description=role.description, permissions=role.permissions,
                    is_system=role.is_system, editable=True)  # fmt: skip
