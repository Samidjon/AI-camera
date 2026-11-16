"""Permission catalog. Roles hold lists of these strings ('*' = all). Enforced by the API and, for
camera / AI operations, by edge agents (from the access token's ``perms`` claim)."""

from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "cameras:read": "View cameras, live video and camera status",
    "cameras:control": "Connect, disconnect and test cameras",
    "cameras:manage": "Add, edit and delete cameras, groups, zones and lines",
    "events:read": "View events and statistics",
    "events:ack": "Acknowledge events (alerts, MVP 08)",
    "events:export": "Export events as CSV",
    "observations:read": "View anonymous person observations (AI)",
    "devices:read": "View edge devices and their health",
    "devices:manage": "Enroll, configure, revoke and delete edge devices",
    "ai:manage": "Enable, disable and configure the AI engine",
    "users:manage": "Manage users and role permissions",
    "settings:manage": "Change system settings (retention, privacy)",
}

BUILTIN_ROLE_DEFAULTS: dict[str, list[str]] = {
    "administrator": ["*"],
    "operator": [
        "cameras:read", "cameras:control", "events:read", "events:ack", "events:export",
        "observations:read", "devices:read",
    ],
    "viewer": ["cameras:read", "events:read", "devices:read"],
}  # fmt: skip


def has(permissions: list[str] | set[str] | None, permission: str) -> bool:
    perms = set(permissions or [])
    return "*" in perms or permission in perms
