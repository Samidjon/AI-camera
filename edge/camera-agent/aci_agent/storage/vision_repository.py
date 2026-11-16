"""Local configuration storage for vision: per-camera settings, zones and crossing lines."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from aci_agent.standard_engine.config import VisionConfig
from aci_agent.standard_engine.geometry import validate_polygon
from aci_agent.standard_engine.rules import CrossingLine, Zone
from aci_agent.storage.database import Database
from aci_agent.storage.repository import DuplicateNameError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check_point(x: float, y: float) -> tuple[float, float]:
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError("Line coordinates must be normalized to 0..1")
    return float(x), float(y)


class VisionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------- config

    def get_config(self, camera_id: str) -> VisionConfig:
        rows = self._db.query("SELECT config FROM vision_configs WHERE camera_id = ?", (camera_id,))
        if not rows:
            return VisionConfig()
        try:
            return VisionConfig.model_validate_json(rows[0]["config"])
        except ValueError:
            return VisionConfig()  # corrupted/old config: fall back to safe defaults

    def save_config(self, camera_id: str, config: VisionConfig) -> VisionConfig:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO vision_configs (camera_id, config, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(camera_id) DO UPDATE SET config = excluded.config, updated_at = excluded.updated_at",
                (camera_id, config.model_dump_json(), _now()),
            )
        return config

    # ------------------------------------------------------------- zones

    @staticmethod
    def _zone(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "camera_id": row["camera_id"], "name": row["name"], "role": row["role"],
            "polygon": json.loads(row["polygon"]), "is_enabled": bool(row["is_enabled"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }  # fmt: skip

    def list_zones(self, camera_id: str) -> list[dict]:
        rows = self._db.query("SELECT * FROM zones WHERE camera_id = ? ORDER BY created_at", (camera_id,))
        return [self._zone(r) for r in rows]

    def get_zone(self, camera_id: str, zone_id: str) -> dict | None:
        rows = self._db.query("SELECT * FROM zones WHERE camera_id = ? AND id = ?", (camera_id, zone_id))
        return self._zone(rows[0]) if rows else None

    def save_zone(
        self, camera_id: str, name: str, role: str, polygon, is_enabled: bool, zone_id: str | None = None
    ) -> dict:
        if role not in ("monitor", "exclude"):
            raise ValueError("Zone role must be 'monitor' or 'exclude'")
        poly = validate_polygon([tuple(p) for p in polygon])
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("Zone name must be 1–80 characters")
        now = _now()
        try:
            with self._db.transaction() as conn:
                if zone_id is None:
                    zone_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO zones (id, camera_id, name, role, polygon, is_enabled, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (zone_id, camera_id, name, role, json.dumps(poly), int(is_enabled), now, now),
                    )
                else:
                    conn.execute(
                        "UPDATE zones SET name = ?, role = ?, polygon = ?, is_enabled = ?, updated_at = ? "
                        "WHERE id = ? AND camera_id = ?",
                        (name, role, json.dumps(poly), int(is_enabled), now, zone_id, camera_id),
                    )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateNameError(f"Zone '{name}' already exists on this camera") from exc
            raise ValueError("Camera not found") from exc
        zone = self.get_zone(camera_id, zone_id)
        assert zone is not None
        return zone

    def delete_zone(self, camera_id: str, zone_id: str) -> bool:
        with self._db.transaction() as conn:
            return conn.execute("DELETE FROM zones WHERE id = ? AND camera_id = ?", (zone_id, camera_id)).rowcount > 0

    def zones_for_engine(self, camera_id: str) -> list[Zone]:
        return [
            Zone(z["id"], z["name"], z["role"], [tuple(p) for p in z["polygon"]], z["is_enabled"])
            for z in self.list_zones(camera_id)
        ]

    # ------------------------------------------------------------- lines

    @staticmethod
    def _line(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "camera_id": row["camera_id"], "name": row["name"],
            "a": [row["ax"], row["ay"]], "b": [row["bx"], row["by"]], "is_enabled": bool(row["is_enabled"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }  # fmt: skip

    def list_lines(self, camera_id: str) -> list[dict]:
        rows = self._db.query("SELECT * FROM crossing_lines WHERE camera_id = ? ORDER BY created_at", (camera_id,))
        return [self._line(r) for r in rows]

    def get_line(self, camera_id: str, line_id: str) -> dict | None:
        rows = self._db.query("SELECT * FROM crossing_lines WHERE camera_id = ? AND id = ?", (camera_id, line_id))
        return self._line(rows[0]) if rows else None

    def save_line(self, camera_id: str, name: str, a, b, is_enabled: bool, line_id: str | None = None) -> dict:
        (ax, ay), (bx, by) = _check_point(*a), _check_point(*b)
        if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 < 0.02:
            raise ValueError("Line is too short")
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("Line name must be 1–80 characters")
        now = _now()
        try:
            with self._db.transaction() as conn:
                if line_id is None:
                    line_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO crossing_lines (id, camera_id, name, ax, ay, bx, by, is_enabled, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (line_id, camera_id, name, ax, ay, bx, by, int(is_enabled), now, now),
                    )
                else:
                    conn.execute(
                        "UPDATE crossing_lines SET name = ?, ax = ?, ay = ?, bx = ?, by = ?, is_enabled = ?, updated_at = ? "
                        "WHERE id = ? AND camera_id = ?",
                        (name, ax, ay, bx, by, int(is_enabled), now, line_id, camera_id),
                    )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateNameError(f"Line '{name}' already exists on this camera") from exc
            raise ValueError("Camera not found") from exc
        line = self.get_line(camera_id, line_id)
        assert line is not None
        return line

    def delete_line(self, camera_id: str, line_id: str) -> bool:
        with self._db.transaction() as conn:
            return (
                conn.execute("DELETE FROM crossing_lines WHERE id = ? AND camera_id = ?", (line_id, camera_id)).rowcount
                > 0
            )

    def lines_for_engine(self, camera_id: str) -> list[CrossingLine]:
        return [
            CrossingLine(ln["id"], ln["name"], tuple(ln["a"]), tuple(ln["b"]), ln["is_enabled"])
            for ln in self.list_lines(camera_id)
        ]
