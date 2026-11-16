"""Alerts, alert rules and e-mail notification settings (MVP 08)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update

from app.alerts.engine import KIND_INFO, METRIC_KINDS, send_email_smtp
from app.api.deps import DbDep, SettingsDep, require_permission
from app.core.errors import AppError, NotFoundError
from app.models import Alert, AlertRule, AuditLog, EdgeDevice, NotificationDelivery, User
from app.schemas.devices import UTCDateTime

router = APIRouter()
rules_router = APIRouter()
CanRead = Annotated[User, Depends(require_permission("events:read"))]
CanAck = Annotated[User, Depends(require_permission("events:ack"))]
CanManage = Annotated[User, Depends(require_permission("settings:manage"))]
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
Kind = Literal[tuple(KIND_INFO)]  # type: ignore[valid-type]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rule_id: uuid.UUID | None
    kind: str
    severity: str
    title: str
    message: str
    device_id: uuid.UUID | None
    camera_ref: str | None
    context: dict
    status: str
    created_at: UTCDateTime
    acknowledged_at: UTCDateTime | None
    resolved_at: UTCDateTime | None


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: Kind
    enabled: bool = True
    severity: Literal["info", "warning", "critical"] | None = None
    threshold: float | None = Field(default=None, ge=0, le=100_000)
    device_id: uuid.UUID | None = None
    camera_ref: str | None = Field(default=None, max_length=64)
    zone_id: str | None = Field(default=None, max_length=64)
    line_id: str | None = Field(default=None, max_length=64)
    cooldown_s: int = Field(default=300, ge=0, le=86_400)
    channels: list[Literal["in_app", "email"]] = Field(default_factory=lambda: ["in_app"], max_length=2)
    email_recipients: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("email_recipients")
    @classmethod
    def _emails(cls, v: list[str]) -> list[str]:
        cleaned = [e.strip().lower() for e in v if e.strip()]
        bad = [e for e in cleaned if not EMAIL.match(e) or len(e) > 255]
        if bad:
            raise ValueError(f"invalid e-mail address: {bad[0][:60]}")
        return sorted(set(cleaned))


class RuleOut(RuleIn):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: uuid.UUID
    severity: str
    created_at: UTCDateTime
    updated_at: UTCDateTime


# ---------------------------------------------------------------------------- alerts


@router.get("", response_model=list[AlertOut])
def list_alerts(
    _: CanRead,
    db: DbDep,
    status: Annotated[Literal["open", "acknowledged", "resolved", "active"] | None, Query()] = None,
    severity: Annotated[Literal["info", "warning", "critical"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    q = select(Alert)
    if status == "active":
        q = q.where(Alert.status != "resolved")
    elif status:
        q = q.where(Alert.status == status)
    if severity:
        q = q.where(Alert.severity == severity)
    return list(db.scalars(q.order_by(Alert.created_at.desc()).limit(limit)))


@router.get("/counts")
def counts(_: CanRead, db: DbDep):
    rows = dict(db.execute(select(Alert.status, func.count()).group_by(Alert.status)).all())
    return {
        "open": rows.get("open", 0),
        "acknowledged": rows.get("acknowledged", 0),
        "resolved": rows.get("resolved", 0),
    }


def _alert(db, alert_id: uuid.UUID) -> Alert:
    a = db.get(Alert, alert_id)
    if a is None:
        raise NotFoundError("Alert not found")
    return a


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(alert_id: uuid.UUID, user: CanAck, db: DbDep):
    a = _alert(db, alert_id)
    if a.status == "open":
        a.status, a.acknowledged_at, a.acknowledged_by = "acknowledged", datetime.now(UTC), user.id
    return a


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: uuid.UUID, user: CanAck, db: DbDep):
    a = _alert(db, alert_id)
    now = datetime.now(UTC)
    if a.acknowledged_at is None:
        a.acknowledged_at, a.acknowledged_by = now, user.id
    a.status, a.resolved_at = "resolved", now
    return a


@router.post("/acknowledge-all")
def acknowledge_all(user: CanAck, db: DbDep):
    n = db.execute(
        update(Alert).where(Alert.status == "open")
        .values(status="acknowledged", acknowledged_at=datetime.now(UTC), acknowledged_by=user.id)
        .execution_options(synchronize_session=False)
    ).rowcount  # fmt: skip
    return {"acknowledged": n or 0}


@router.get("/{alert_id}/deliveries")
def deliveries(alert_id: uuid.UUID, _: CanRead, db: DbDep):
    _alert(db, alert_id)
    rows = db.scalars(select(NotificationDelivery).where(NotificationDelivery.alert_id == alert_id))
    return [{"channel": d.channel, "recipient": d.recipient, "status": d.status, "attempts": d.attempts,
             "error": d.error, "sent_at": d.sent_at} for d in rows]  # fmt: skip


# ---------------------------------------------------------------------------- rules


@rules_router.get("/catalog")
def catalog(_: CanRead, settings: SettingsDep):
    return {
        "kinds": [{"kind": k, **v, "metric": k in METRIC_KINDS} for k, v in KIND_INFO.items()],
        "email": {"configured": settings.smtp_configured, "from": settings.smtp_from or settings.smtp_username or None},
    }


@rules_router.get("", response_model=list[RuleOut])
def list_rules(_: CanRead, db: DbDep):
    return list(db.scalars(select(AlertRule).order_by(AlertRule.created_at)))


def _check(db, body: RuleIn) -> None:
    if body.device_id and db.get(EdgeDevice, body.device_id) is None:
        raise NotFoundError("Device not found")
    if "email" in body.channels and not body.email_recipients:
        raise AppError("Add at least one e-mail recipient for the e-mail channel", code="recipients_required")


@rules_router.post("", response_model=RuleOut, status_code=201)
def create_rule(body: RuleIn, user: CanManage, db: DbDep):
    _check(db, body)
    data = body.model_dump()
    data["severity"] = data["severity"] or KIND_INFO[body.kind]["severity"]
    rule = AlertRule(**data, created_by=user.id)
    db.add(rule)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="alert_rule.created", entity_type="alert_rule", entity_id=str(rule.id),
                    details={"kind": rule.kind, "channels": rule.channels}))  # fmt: skip
    return rule


@rules_router.put("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, body: RuleIn, user: CanManage, db: DbDep):
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise NotFoundError("Alert rule not found")
    _check(db, body)
    for k, v in body.model_dump().items():
        setattr(rule, k, v if not (k == "severity" and v is None) else KIND_INFO[body.kind]["severity"])
    db.add(AuditLog(user_id=user.id, action="alert_rule.updated", entity_type="alert_rule", entity_id=str(rule.id)))
    db.flush()
    db.refresh(rule)
    return rule


@rules_router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, user: CanManage, db: DbDep):
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise NotFoundError("Alert rule not found")
    db.delete(rule)  # raised alerts stay (rule_id → NULL)
    db.add(AuditLog(user_id=user.id, action="alert_rule.deleted", entity_type="alert_rule", entity_id=str(rule_id)))
    return Response(status_code=204)


RECOMMENDED = [
    ("Camera offline", "camera_offline", None), ("Camera reconnected", "camera_reconnected", None),
    ("Stream error", "stream_error", None), ("AI engine offline", "ai_offline", None),
    ("AI recovered", "ai_recovered", None), ("Model loading error", "model_error", None),
    ("Device offline", "device_offline", None), ("High CPU (≥ 90 %)", "high_cpu", 90),
    ("High memory (≥ 90 %)", "high_memory", 90), ("High temperature (≥ 85 °C)", "high_temperature", 85),
    ("Low disk (≤ 5 GB free)", "low_disk", 5),
]  # fmt: skip


@rules_router.post("/recommended", response_model=list[RuleOut], status_code=201)
def add_recommended(user: CanManage, db: DbDep):
    """Health rules with in-app notifications only (motion rules are noisy — add them per camera)."""
    existing = set(db.scalars(select(AlertRule.kind)))
    created = []
    for name, kind, threshold in RECOMMENDED:
        if kind in existing:
            continue
        rule = AlertRule(name=name, kind=kind, enabled=True, severity=KIND_INFO[kind]["severity"], threshold=threshold,
                         cooldown_s=600, channels=["in_app"], email_recipients=[], created_by=user.id)  # fmt: skip
        db.add(rule)
        created.append(rule)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="alert_rule.recommended_added", details={"count": len(created)}))
    return created


class TestEmailIn(BaseModel):
    to: str = Field(max_length=255)


@rules_router.post("/test-email")
def test_email(body: TestEmailIn, request: Request, user: CanManage, settings: SettingsDep):
    if not EMAIL.match(body.to.strip()):
        raise AppError("Invalid e-mail address", code="invalid_email")
    if not settings.smtp_configured:
        raise AppError(
            "SMTP is not configured on the server (ACI_SMTP_HOST, ACI_SMTP_FROM …)", code="smtp_not_configured"
        )
    try:
        send_email_smtp(settings, body.to.strip(), "AI Camera Intelligence — test notification",
                        "This is a test e-mail. Alert e-mails will look like this.")  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        raise AppError(f"Sending failed: {type(exc).__name__}: {str(exc)[:150]}", code="smtp_failed") from None
    return {"sent": True}
