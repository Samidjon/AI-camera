from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Deliberately lenient: on-premise surveillance deployments often use intranet domains
# (e.g. "admin@site.local") that strict RFC/IANA validators reject.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+$"
LoginEmail = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=3, max_length=255, pattern=EMAIL_PATTERN)
]


class LoginRequest(BaseModel):
    email: LoginEmail
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    permissions: list[str]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    role: RoleOut
    last_login_at: datetime | None
    created_at: datetime
