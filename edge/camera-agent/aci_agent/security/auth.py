"""Authorization for the agent's management API.

Dashboard users authenticate against the central server and present its JWT here. The agent
verifies the signature locally with the shared secret (no network call, so management works
while the central server is down) and reads the ``perms`` claim:

* ``cameras:read``    — list cameras, live preview
* ``cameras:control`` — connect / disconnect / test
* ``cameras:manage``  — add / edit / delete cameras and groups
* ``*``               — everything (administrator)

Live preview uses short-lived, camera-scoped *stream tokens* because <img> tags can't send
Authorization headers; the long-lived access token never appears in a URL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from aci_agent.configuration.settings import AgentSettings


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class Principal:
    def __init__(self, subject: str, role: str | None, permissions: list[str]) -> None:
        self.subject, self.role, self.permissions = subject, role, set(permissions)

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def _decode(token: str, settings: AgentSettings) -> dict[str, Any]:
    if not settings.auth_configured:
        raise AuthError("Agent authentication is not configured (ACI_AGENT_JWT_SECRET)", 503)
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid token") from exc


def principal_from_access_token(token: str, settings: AgentSettings) -> Principal:
    payload = _decode(token, settings)
    if payload.get("type") != "access" or not payload.get("sub"):
        raise AuthError("Not an access token")
    perms = payload.get("perms")
    if not isinstance(perms, list):
        raise AuthError("Token has no permissions — sign in again", 401)
    return Principal(str(payload["sub"]), payload.get("role"), [str(p) for p in perms])


def create_stream_token(subject: str, camera_id: str, settings: AgentSettings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "cam": camera_id,
        "type": "stream",
        "iat": now,
        "exp": now + timedelta(seconds=settings.stream_token_ttl_s),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_stream_token(token: str, camera_id: str, settings: AgentSettings) -> str:
    payload = _decode(token, settings)
    if payload.get("type") != "stream" or payload.get("cam") != camera_id:
        raise AuthError("Stream token is not valid for this camera", 403)
    return str(payload.get("sub"))
