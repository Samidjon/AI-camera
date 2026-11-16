"""Process-wide alert engine + transaction hooks.

Alerts raised inside a database transaction are announced (WebSocket) and e-mailed only after that
transaction commits — and dropped if it rolls back — via SQLAlchemy session events. So every code
path (event upload, heartbeat, background device monitor) behaves the same without remembering to
publish.
"""

from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.alerts.engine import AlertEngine

logger = logging.getLogger("aci.alerts")
engine: AlertEngine | None = None


def install(alert_engine: AlertEngine | None) -> None:
    global engine
    engine = alert_engine


def evaluate_events(db: Session, device, events: list[dict]) -> None:
    if engine is not None and events:
        try:
            engine.on_events(db, device, events)
        except Exception as exc:  # noqa: BLE001 - alerting must never break ingestion
            logger.error("Alert evaluation failed: %s", type(exc).__name__)


def evaluate_heartbeat(db: Session, device) -> None:
    if engine is not None:
        try:
            engine.on_heartbeat(db, device)
        except Exception as exc:  # noqa: BLE001
            logger.error("Alert evaluation failed: %s", type(exc).__name__)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    pending = session.info.pop("pending_alerts", None)
    if pending and engine is not None:
        engine.publish(pending)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("pending_alerts", None)
