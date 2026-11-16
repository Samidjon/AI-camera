"""Authentication service: credential check, login bookkeeping, user creation."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, AuthenticationError, ConflictError, NotFoundError
from app.core.security import hash_password, verify_password
from app.models import AuditLog, Role, User
from app.schemas.auth import EMAIL_PATTERN

logger = logging.getLogger("aci.auth")

# Pre-computed hash used to keep timing similar for unknown users.
_DUMMY_HASH = hash_password("timing-equalizer")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def authenticate(db: Session, email: str, password: str, ip: str | None = None) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise AuthenticationError("Invalid email or password")
    if not verify_password(password, user.password_hash):
        db.add(AuditLog(user_id=user.id, action="auth.login_failed", ip_address=ip))
        db.commit()  # persist the audit record even though the request fails
        raise AuthenticationError("Invalid email or password")
    if not user.is_active:
        raise AuthenticationError("Account is disabled")
    user.last_login_at = datetime.now(UTC)
    db.add(AuditLog(user_id=user.id, action="auth.login", ip_address=ip))
    logger.info("User logged in", extra={"user_id": str(user.id)})
    return user


def create_user(db: Session, *, email: str, password: str, role_name: str, full_name: str | None) -> User:
    email = normalize_email(email)
    if not re.fullmatch(EMAIL_PATTERN, email) or len(email) > 255:
        raise AppError(f"Invalid email address: {email}", code="invalid_email")
    if db.scalar(select(User.id).where(User.email == email)):
        raise ConflictError(f"User {email} already exists")
    role = db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise NotFoundError(f"Role '{role_name}' not found — run migrations first")
    user = User(email=email, full_name=full_name, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="user.created", entity_type="user", entity_id=str(user.id)))
    return user
