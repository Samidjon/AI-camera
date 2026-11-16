"""Alert Engine (MVP 08).

Alerts are raised **only by rules the user created** (Settings → Alerts). Inputs:

* stored events (after the event processor accepted them): camera / AI / device health, motion,
  zone and line events;
* heartbeats: CPU, memory, temperature, free disk against the rule's threshold.

Each rule has a cooldown per scope (rule + device + camera + zone/line), so a flapping camera or a
busy zone doesn't flood anyone. Channels: ``in_app`` (alerts list, top-bar bell, WebSocket) and
``email`` (SMTP from the server configuration). An e-mail is sent only when the rule has the email
channel, at least one recipient and SMTP is configured — otherwise the delivery is recorded as
``skipped`` with the reason. Delivery runs in a background thread with retries; ingestion never
waits for SMTP.
"""

from __future__ import annotations

import logging
import queue
import smtplib
import ssl
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Alert, AlertKind, AlertRule, Camera, EdgeDevice, NotificationDelivery
from app.realtime.hub import hub

logger = logging.getLogger("aci.alerts")

KIND_INFO: dict[str, dict] = {
    "camera_offline": {"group": "Camera", "label": "Camera offline", "severity": "critical"},
    "camera_reconnected": {"group": "Camera", "label": "Camera reconnected", "severity": "info"},
    "stream_error": {"group": "Camera", "label": "Stream error", "severity": "warning"},
    "ai_offline": {"group": "AI", "label": "AI engine offline", "severity": "warning"},
    "ai_recovered": {"group": "AI", "label": "AI recovered", "severity": "info"},
    "model_error": {"group": "AI", "label": "Model loading error", "severity": "critical"},
    "high_cpu": {"group": "Device", "label": "High CPU usage", "severity": "warning", "threshold": 90, "unit": "%"},
    "high_memory": {
        "group": "Device",
        "label": "High memory usage",
        "severity": "warning",
        "threshold": 90,
        "unit": "%",
    },  # fmt: skip
    "high_temperature": {
        "group": "Device",
        "label": "High temperature",
        "severity": "critical",
        "threshold": 85,
        "unit": "°C",
    },  # fmt: skip
    "low_disk": {
        "group": "Device",
        "label": "Low disk space",
        "severity": "critical",
        "threshold": 5,
        "unit": "GB free",
    },  # fmt: skip
    "device_offline": {"group": "Device", "label": "Device offline", "severity": "critical"},
    "motion_detected": {"group": "Motion", "label": "Motion detected", "severity": "info"},
    "zone_activity": {"group": "Motion", "label": "Zone activity", "severity": "info"},
    "line_crossed": {"group": "Motion", "label": "Line crossed", "severity": "info"},
}
assert set(KIND_INFO) == {k.value for k in AlertKind}

EVENT_KINDS: dict[str, tuple[str, ...]] = {
    "camera_error": ("camera_offline", "stream_error"),
    "camera_recovered": ("camera_reconnected",),
    "ai_error": ("ai_offline", "model_error"),
    "ai_recovered": ("ai_recovered",),
    "device_offline": ("device_offline",),
    "motion_started": ("motion_detected",),
    "object_entered_zone": ("zone_activity",),
    "line_crossed": ("line_crossed",),
    "person_crossed_line": ("line_crossed",),
}
METRIC_KINDS = ("high_cpu", "high_memory", "high_temperature", "low_disk")
OFFLINE_FAILURES = {"unreachable", "auth_failed", "not_found", "invalid_source"}
RECOVERED_RESOLVES = {"camera_reconnected": ("camera_offline", "stream_error"), "ai_recovered": ("ai_offline",)}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def event_matches(kind: str, event: dict) -> bool:
    """Split one event type into the alert kinds it means (exactly one for health events)."""
    meta = event.get("metadata") or {}
    if kind in ("camera_offline", "stream_error"):
        offline = meta.get("failure_kind") in OFFLINE_FAILURES or meta.get("status") == "error"
        return offline if kind == "camera_offline" else not offline
    if kind in ("ai_offline", "model_error"):
        model = "model" in str(meta.get("reason") or "").lower()
        return model if kind == "model_error" else not model
    return True


def metric_value(kind: str, device: EdgeDevice) -> float | None:
    hw = (device.metrics or {}).get("hardware") or {}
    try:
        if kind == "high_cpu":
            return float(hw["cpu"]["percent"])
        if kind == "high_memory":
            return float(hw["memory"]["percent"])
        if kind == "high_temperature":
            t = hw.get("temperature") or {}
            return float(t["max_c"]) if t.get("available") else None
        if kind == "low_disk":
            return float(hw["disk"]["free_gb"])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def metric_breached(kind: str, value: float, threshold: float) -> bool:
    return value <= threshold if kind == "low_disk" else value >= threshold


class AlertEngine:
    def __init__(self, settings: Settings, notifier: Notifier | None = None) -> None:
        self.settings = settings
        self.notifier = notifier

    # ------------------------------------------------------------------ evaluation

    def _rules(self, db: Session, kinds) -> list[AlertRule]:
        return list(db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.kind.in_(list(kinds)))))

    @staticmethod
    def _in_scope(rule: AlertRule, device_id, camera_ref, zone_id=None, line_id=None) -> bool:
        return (
            (rule.device_id is None or rule.device_id == device_id)
            and (rule.camera_ref is None or rule.camera_ref == camera_ref)
            and (rule.zone_id is None or rule.zone_id == zone_id)
            and (rule.line_id is None or rule.line_id == line_id)
        )

    def on_events(self, db: Session, device: EdgeDevice, events: list[dict]) -> list[Alert]:
        relevant = [e for e in events if e.get("event_type") in EVENT_KINDS]
        if not relevant:
            return []
        kinds = {k for e in relevant for k in EVENT_KINDS[e["event_type"]]}
        rules = self._rules(db, kinds)
        if not rules:
            return []
        names = self._camera_names(db, device)
        out: list[Alert] = []
        for e in relevant:
            for rule in rules:
                if rule.kind not in EVENT_KINDS[e["event_type"]] or not event_matches(rule.kind, e):
                    continue
                cam = e.get("camera_ref")
                if not self._in_scope(rule, device.id, cam, e.get("zone_id"), e.get("line_id")):
                    continue
                alert = self._raise(db, rule, device, cam, names.get(cam), e)
                if alert:
                    out.append(alert)
        return out

    def on_heartbeat(self, db: Session, device: EdgeDevice) -> list[Alert]:
        out = []
        for rule in self._rules(db, METRIC_KINDS):
            if not self._in_scope(rule, device.id, None):
                continue
            value = metric_value(rule.kind, device)
            threshold = rule.threshold if rule.threshold is not None else KIND_INFO[rule.kind]["threshold"]
            if value is None or not metric_breached(rule.kind, value, threshold):
                continue
            alert = self._raise(db, rule, device, None, None, {"value": value, "threshold": threshold})
            if alert:
                out.append(alert)
        return out

    # ------------------------------------------------------------------ raising

    @staticmethod
    def _camera_names(db: Session, device: EdgeDevice) -> dict[str, str]:
        return dict(db.execute(select(Camera.external_id, Camera.name).where(Camera.device_id == device.id)).all())

    def _raise(self, db: Session, rule: AlertRule, device: EdgeDevice, camera_ref: str | None,
               camera_name: str | None, payload: dict) -> Alert | None:  # fmt: skip
        scope = f"{device.id}|{camera_ref or ''}|{payload.get('zone_id') or ''}|{payload.get('line_id') or ''}"
        last = db.scalar(
            select(Alert.created_at)
            .where(Alert.rule_id == rule.id, Alert.scope_key == scope)
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        now = _utcnow()
        if last is not None and now - _aware(last) < timedelta(seconds=rule.cooldown_s):
            return None  # cooldown: one alert per scope per period
        info = KIND_INFO[rule.kind]
        where = " · ".join(x for x in (camera_name or camera_ref, device.name) if x)
        title = f"{info['label']}: {where}"[:200]
        message = self._message(rule, payload, camera_name or camera_ref, device.name)
        alert = Alert(
            id=uuid.uuid4(), rule_id=rule.id, kind=rule.kind, severity=rule.severity, title=title, message=message,
            device_id=device.id, camera_ref=camera_ref, scope_key=scope, status="open", created_at=now,
            context={k: payload.get(k) for k in ("event_type", "id", "zone_id", "line_id", "value", "threshold")
                     if payload.get(k) is not None} | {"rule_name": rule.name},
        )  # fmt: skip
        db.add(alert)
        db.flush()
        self._resolve_previous(db, rule.kind, device.id, camera_ref, now)
        channels = set(rule.channels or [])
        email_pending = False
        if "in_app" in channels:
            db.add(NotificationDelivery(alert_id=alert.id, channel="in_app", status="sent", attempts=1, sent_at=now))
        if "email" in channels:
            recipients = [r for r in (rule.email_recipients or []) if isinstance(r, str) and "@" in r]
            if not recipients:
                db.add(NotificationDelivery(alert_id=alert.id, channel="email", status="skipped",
                                            error="rule has no e-mail recipients"))  # fmt: skip
            elif not self.settings.smtp_configured:
                for r in recipients:
                    db.add(NotificationDelivery(alert_id=alert.id, channel="email", recipient=r, status="skipped",
                                                error="SMTP is not configured on the server"))  # fmt: skip
            else:
                for r in recipients:
                    db.add(NotificationDelivery(alert_id=alert.id, channel="email", recipient=r, status="pending"))
                email_pending = True
        db.info.setdefault("pending_alerts", []).append({
            "id": str(alert.id), "kind": alert.kind, "severity": alert.severity, "title": alert.title,
            "message": alert.message, "device_id": str(device.id), "camera_ref": camera_ref,
            "created_at": now.isoformat(), "email_pending": email_pending,
        })  # fmt: skip
        return alert

    @staticmethod
    def _resolve_previous(db: Session, kind: str, device_id, camera_ref, now) -> None:
        """'Camera reconnected' / 'AI recovered' close the matching open problem alerts."""
        for problem in RECOVERED_RESOLVES.get(kind, ()):
            open_alerts = select(Alert).where(
                Alert.kind == problem,
                Alert.device_id == device_id,
                Alert.camera_ref == camera_ref,
                Alert.status != "resolved",
            )
            for a in db.scalars(open_alerts):
                a.status, a.resolved_at = "resolved", now

    @staticmethod
    def _message(rule: AlertRule, payload: dict, camera: str | None, device: str) -> str:
        meta = payload.get("metadata") or {}
        base = {
            "camera_offline": f"Camera {camera} stopped streaming ({meta.get('failure_kind') or meta.get('status')}).",
            "stream_error": f"Stream problem on camera {camera}: {meta.get('error') or meta.get('failure_kind')}.",
            "camera_reconnected": f"Camera {camera} is streaming again.",
            "ai_offline": f"AI engine on {device} is {meta.get('mode')}: {meta.get('reason') or ''}".strip(),
            "model_error": f"AI model problem on {device}: {meta.get('reason')}",
            "ai_recovered": f"AI engine on {device} is active again.",
            "device_offline": f"Device {device} stopped sending heartbeats ({meta.get('reason') or ''}).",
            "motion_detected": f"Motion detected on camera {camera}.",
            "zone_activity": f"A moving object entered zone {meta.get('zone_name') or payload.get('zone_id')} "
            f"on camera {camera}.",
            "line_crossed": f"{'Person' if payload.get('source') == 'ai' else 'Moving object'} crossed line "
            f"{meta.get('line_name') or payload.get('line_id')} ({meta.get('direction')}) on camera {camera}.",
        }
        if rule.kind in METRIC_KINDS:
            info = KIND_INFO[rule.kind]
            value, threshold = payload["value"], payload["threshold"]
            return f"{info['label']} on {device}: {value:g} {info['unit']} (threshold {threshold:g})."
        return base.get(rule.kind, rule.name)

    # ------------------------------------------------------------------ after commit

    def publish(self, alerts: list[dict]) -> None:
        """Runs after the transaction committed (see ``app.alerts.runtime``): live notice + e-mails."""
        for a in alerts:
            hub.publish("alert.created", a)
            if self.notifier and a.get("email_pending"):
                self.notifier.submit(uuid.UUID(a["id"]))


# ---------------------------------------------------------------------------- e-mail


def send_email_smtp(settings: Settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_username
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if settings.smtp_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15,
                              context=ssl.create_default_context()) as s:  # fmt: skip
            if settings.smtp_username:
                s.login(settings.smtp_username, settings.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            if settings.smtp_starttls:
                s.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                s.login(settings.smtp_username, settings.smtp_password)
            s.send_message(msg)


class Notifier:
    """Background e-mail delivery with retries (3 attempts, 5 / 30 s apart)."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Callable,
        sender: Callable[[Settings, str, str, str], None] = send_email_smtp,
        retry_delays: tuple[float, ...] = (5, 30),
    ) -> None:
        self.settings = settings
        self._session = session_factory
        self._send = sender
        self._delays = retry_delays
        self._queue: queue.Queue[uuid.UUID | None] = queue.Queue(maxsize=10_000)
        self._thread: threading.Thread | None = None
        self.sent = 0
        self.failed = 0

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="alert-notifier", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(5)
            self._thread = None

    def submit(self, alert_id: uuid.UUID) -> None:
        try:
            self._queue.put_nowait(alert_id)
        except queue.Full:
            logger.error("Notification queue full — e-mails of alert %s not sent", alert_id)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self.deliver_alert(item)
            except Exception as exc:  # noqa: BLE001 - the notifier thread must survive
                logger.error("Notification delivery crashed: %s", type(exc).__name__)

    def deliver_alert(self, alert_id: uuid.UUID) -> list[str]:
        with self._session(self.settings) as db:
            ids = list(db.scalars(select(NotificationDelivery.id).where(
                NotificationDelivery.alert_id == alert_id, NotificationDelivery.status == "pending")))  # fmt: skip
        return [self.deliver(i) for i in ids]

    def deliver(self, delivery_id: int) -> str:
        """Send one pending delivery now (with retries). Returns the final status."""
        for attempt in range(len(self._delays) + 1):
            with self._session(self.settings) as db:
                d = db.get(NotificationDelivery, delivery_id)
                if d is None or d.status != "pending":
                    return d.status if d else "missing"
                alert = db.get(Alert, d.alert_id)
                d.attempts += 1
                try:
                    body = (
                        f"{alert.message}\n\nSeverity: {alert.severity}\nTime: {_aware(alert.created_at).isoformat()}"
                    )
                    if self.settings.public_dashboard_url:
                        body += f"\nOpen: {self.settings.public_dashboard_url.rstrip('/')}/alerts"
                    body += "\n\n— AI Camera Intelligence (you receive this because an alert rule lists your address)"
                    self._send(self.settings, d.recipient, f"[{alert.severity.upper()}] {alert.title}", body)
                    d.status, d.sent_at, d.error = "sent", _utcnow(), None
                    self.sent += 1
                    return "sent"
                except Exception as exc:  # noqa: BLE001 - any SMTP problem is a delivery failure
                    d.error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    final = attempt >= len(self._delays)
                    if final:
                        d.status = "failed"
                        self.failed += 1
                        logger.warning("E-mail delivery %s failed: %s", delivery_id, d.error)
                        return "failed"
            threading.Event().wait(self._delays[attempt])
        return "failed"
