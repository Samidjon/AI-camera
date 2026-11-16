from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.permissions import BUILTIN_ROLE_DEFAULTS
from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin

# Built-in roles, seeded by the initial migration. Permissions are enforced from MVP 07 on.
BUILTIN_ROLES: dict[str, dict] = {
    "administrator": {"description": "Full system access", "permissions": BUILTIN_ROLE_DEFAULTS["administrator"]},
    "operator": {"description": "Operate cameras and handle events", "permissions": BUILTIN_ROLE_DEFAULTS["operator"]},
    "viewer": {"description": "Read-only access", "permissions": BUILTIN_ROLE_DEFAULTS["viewer"]},
}


class Role(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    users: Mapped[list[User]] = relationship(back_populates="role")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")
