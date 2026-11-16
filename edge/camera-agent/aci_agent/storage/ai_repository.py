"""Persistence for the AI engine: operator configuration and status history (bounded)."""

from __future__ import annotations

from datetime import UTC, datetime

from aci_agent.ai_engine.configuration import AIRuntimeConfig
from aci_agent.storage.database import Database

HISTORY_LIMIT = 1000


class AIRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get_config(self) -> AIRuntimeConfig:
        rows = self._db.query("SELECT config FROM ai_config WHERE id = 1")
        if not rows:
            return AIRuntimeConfig()
        try:
            return AIRuntimeConfig.model_validate_json(rows[0]["config"])
        except ValueError:
            return AIRuntimeConfig()

    def save_config(self, config: AIRuntimeConfig) -> AIRuntimeConfig:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO ai_config (id, config, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET config = excluded.config, updated_at = excluded.updated_at",
                (config.model_dump_json(), datetime.now(UTC).isoformat()),
            )
        return config

    def record_status(self, mode: str, reason: str | None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO ai_status_history (at, mode, reason) VALUES (?, ?, ?)",
                (datetime.now(UTC).isoformat(), mode, (reason or "")[:300]),
            )
            conn.execute(
                "DELETE FROM ai_status_history WHERE id <= (SELECT MAX(id) FROM ai_status_history) - ?",
                (HISTORY_LIMIT,),
            )

    def history(self, limit: int = 50) -> list[dict]:
        rows = self._db.query("SELECT at, mode, reason FROM ai_status_history ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
