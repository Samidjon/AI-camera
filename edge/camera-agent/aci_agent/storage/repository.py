"""Repositories for cameras, groups and encrypted camera secrets."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from aci_agent.camera.models import CameraConfig, CameraRuntime, CameraSecret, SourceType
from aci_agent.security.crypto import SecretBox
from aci_agent.storage.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class DuplicateNameError(Exception):
    pass


class CameraRepository:
    def __init__(self, db: Database, secrets: SecretBox) -> None:
        self._db = db
        self._box = secrets

    @property
    def db(self) -> Database:
        return self._db

    # ------------------------------------------------------------- cameras

    @staticmethod
    def _row_to_config(row: sqlite3.Row) -> CameraConfig:
        return CameraConfig(
            id=row["id"],
            name=row["name"],
            location=row["location"],
            source_type=SourceType(row["source_type"]),
            source_uri=row["source_uri"],
            options=json.loads(row["options"] or "{}"),
            group_id=row["group_id"],
            is_enabled=bool(row["is_enabled"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def list(self) -> list[CameraConfig]:
        return [self._row_to_config(r) for r in self._db.query("SELECT * FROM cameras ORDER BY created_at")]

    def get(self, camera_id: str) -> CameraConfig | None:
        rows = self._db.query("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        return self._row_to_config(rows[0]) if rows else None

    def count(self) -> int:
        return int(self._db.query("SELECT COUNT(*) FROM cameras")[0][0])

    def persisted_state(self, camera_id: str) -> dict:
        rows = self._db.query(
            "SELECT resolution_width, resolution_height, fps, last_status, last_error, last_connected_at, last_frame_at "
            "FROM cameras WHERE id = ?",
            (camera_id,),
        )
        return dict(rows[0]) if rows else {}

    def create(self, config: CameraConfig, secret: CameraSecret | None) -> CameraConfig:
        config.id = config.id or str(uuid.uuid4())
        now = _now()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO cameras (id, name, location, source_type, source_uri, options, group_id, is_enabled, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    config.id,
                    config.name,
                    config.location,
                    config.source_type.value,
                    config.source_uri,
                    json.dumps(config.options),
                    config.group_id,
                    int(config.is_enabled),
                    now,
                    now,
                ),
            )
            if secret:
                conn.execute(
                    "INSERT INTO camera_secrets (camera_id, ciphertext, updated_at) VALUES (?, ?, ?)",
                    (config.id, self._box.encrypt_json({"u": secret.username, "p": secret.password}), now),
                )
        created = self.get(config.id)
        assert created is not None
        return created

    def update(self, config: CameraConfig, secret: CameraSecret | None, *, replace_secret: bool) -> CameraConfig:
        now = _now()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE cameras SET name=?, location=?, source_type=?, source_uri=?, options=?, group_id=?, is_enabled=?, "
                "updated_at=? WHERE id=?",
                (
                    config.name,
                    config.location,
                    config.source_type.value,
                    config.source_uri,
                    json.dumps(config.options),
                    config.group_id,
                    int(config.is_enabled),
                    now,
                    config.id,
                ),
            )
            if replace_secret:
                conn.execute("DELETE FROM camera_secrets WHERE camera_id = ?", (config.id,))
                if secret:
                    conn.execute(
                        "INSERT INTO camera_secrets (camera_id, ciphertext, updated_at) VALUES (?, ?, ?)",
                        (config.id, self._box.encrypt_json({"u": secret.username, "p": secret.password}), now),
                    )
        updated = self.get(config.id)
        assert updated is not None
        return updated

    def delete(self, camera_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            return cur.rowcount > 0

    def save_runtime(self, camera_id: str, rt: CameraRuntime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE cameras SET resolution_width = COALESCE(?, resolution_width), "
                "resolution_height = COALESCE(?, resolution_height), fps = COALESCE(?, fps), last_status = ?, "
                "last_error = ?, last_connected_at = COALESCE(?, last_connected_at), "
                "last_frame_at = COALESCE(?, last_frame_at) WHERE id = ?",
                (
                    rt.width,
                    rt.height,
                    rt.fps or rt.nominal_fps,
                    rt.status.value,
                    rt.last_error,
                    rt.last_connected_at.isoformat() if rt.last_connected_at else None,
                    rt.last_frame_at.isoformat() if rt.last_frame_at else None,
                    camera_id,
                ),
            )

    # ------------------------------------------------------------- secrets

    def get_secret(self, camera_id: str) -> CameraSecret | None:
        rows = self._db.query("SELECT ciphertext FROM camera_secrets WHERE camera_id = ?", (camera_id,))
        if not rows:
            return None
        data = self._box.decrypt_json(rows[0]["ciphertext"])
        return CameraSecret(username=data.get("u", ""), password=data.get("p", ""))

    def has_secret(self, camera_id: str) -> bool:
        return bool(self._db.query("SELECT 1 FROM camera_secrets WHERE camera_id = ?", (camera_id,)))

    # ------------------------------------------------------------- groups

    def list_groups(self) -> list[dict]:
        rows = self._db.query(
            "SELECT g.*, (SELECT COUNT(*) FROM cameras c WHERE c.group_id = g.id) AS camera_count "
            "FROM camera_groups g ORDER BY g.name"
        )
        return [dict(r) for r in rows]

    def get_group(self, group_id: str) -> dict | None:
        rows = self._db.query("SELECT * FROM camera_groups WHERE id = ?", (group_id,))
        return dict(rows[0]) if rows else None

    def create_group(self, name: str, description: str | None) -> dict:
        gid, now = str(uuid.uuid4()), _now()
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO camera_groups (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (gid, name, description, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateNameError(f"Group '{name}' already exists") from exc
        group = self.get_group(gid)
        assert group is not None
        return group

    def update_group(self, group_id: str, name: str, description: str | None) -> dict | None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "UPDATE camera_groups SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                    (name, description, _now(), group_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateNameError(f"Group '{name}' already exists") from exc
        return self.get_group(group_id)

    def delete_group(self, group_id: str) -> bool:
        with self._db.transaction() as conn:
            return conn.execute("DELETE FROM camera_groups WHERE id = ?", (group_id,)).rowcount > 0
