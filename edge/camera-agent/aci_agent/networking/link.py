"""Persistent link between this device and a central server (``server_link`` table, one row).

The device secret is encrypted with the agent's Fernet key (same key as camera credentials) and is
never returned by the API or written to logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aci_agent.security.crypto import SecretBox, SecretDecryptionError
from aci_agent.storage.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ServerLink:
    server_url: str
    remote_device_id: str | None
    registered_at: str | None
    revoked_at: str | None
    last_heartbeat_at: str | None
    last_sync_at: str | None
    config_version: int
    config_applied_at: str | None
    has_credentials: bool

    def public(self) -> dict:
        return {
            "server_url": self.server_url,
            "remote_device_id": self.remote_device_id,
            "registered": self.registered_at is not None and self.has_credentials,
            "registered_at": self.registered_at,
            "revoked": self.revoked_at is not None,
            "revoked_at": self.revoked_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_sync_at": self.last_sync_at,
            "config_version": self.config_version,
            "config_applied_at": self.config_applied_at,
        }


class ServerLinkRepository:
    def __init__(self, db: Database, box: SecretBox) -> None:
        self._db = db
        self._box = box

    def get(self) -> ServerLink | None:
        rows = self._db.query("SELECT * FROM server_link WHERE id = 1")
        if not rows:
            return None
        r = rows[0]
        return ServerLink(
            server_url=r["server_url"],
            remote_device_id=r["remote_device_id"],
            registered_at=r["registered_at"],
            revoked_at=r["revoked_at"],
            last_heartbeat_at=r["last_heartbeat_at"],
            last_sync_at=r["last_sync_at"],
            config_version=int(r["config_version"] or 0),
            config_applied_at=r["config_applied_at"],
            has_credentials=r["credentials"] is not None,
        )

    def secret(self) -> str | None:
        rows = self._db.query("SELECT credentials FROM server_link WHERE id = 1")
        if not rows or rows[0]["credentials"] is None:
            return None
        try:
            return str(self._box.decrypt_json(rows[0]["credentials"])["device_secret"])
        except (SecretDecryptionError, KeyError):
            return None

    def save_registration(self, server_url: str, remote_device_id: str, device_secret: str) -> None:
        token = self._box.encrypt_json({"device_secret": device_secret})
        now = _now()
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM server_link WHERE id = 1")
            conn.execute(
                "INSERT INTO server_link (id, server_url, remote_device_id, credentials, registered_at, "
                "revoked_at, config_version, updated_at) VALUES (1, ?, ?, ?, ?, NULL, 0, ?)",
                (server_url, remote_device_id, token, now, now),
            )

    def mark_revoked(self) -> None:
        now = _now()
        # The credential is useless once revoked: delete it rather than keep a secret around.
        self._db.execute(
            "UPDATE server_link SET revoked_at = ?, credentials = NULL, updated_at = ? WHERE id = 1", (now, now)
        )

    def touch(self, field: str) -> None:
        if field not in ("last_heartbeat_at", "last_sync_at"):
            raise ValueError(field)
        now = _now()
        self._db.execute(f"UPDATE server_link SET {field} = ?, updated_at = ? WHERE id = 1", (now, now))

    def set_config_version(self, version: int) -> None:
        now = _now()
        self._db.execute(
            "UPDATE server_link SET config_version = ?, config_applied_at = ?, updated_at = ? WHERE id = 1",
            (version, now, now),
        )

    def delete(self) -> None:
        self._db.execute("DELETE FROM server_link WHERE id = 1")
