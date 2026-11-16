"""MVP 08 — analytics (statistics, time zones, empty data, duplicates, privacy), reports (CSV/PDF),
alert rules, alert engine (firing, cooldown, scope, resolve), e-mail delivery (fake SMTP sender)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.engine import Notifier
from app.analytics import service
from app.db.session import session_scope
from app.models import Alert, NotificationDelivery
from app.services.devices import mark_stale_devices
from tests.integration.test_dashboard_api import person
from tests.integration.test_devices import Agent, enroll, event

API = "/api/v1"


def agent(client: TestClient, admin: dict, name: str = "edge") -> Agent:
    a = Agent(client, name)
    assert a.register(enroll(client, admin)).status_code == 201
    assert a.heartbeat().status_code == 200
    return a


def at(base: datetime, **delta) -> str:
    return (base + timedelta(**delta)).isoformat()


# ---------------------------------------------------------------------------- analytics


def test_analytics_empty_data(client: TestClient, admin: dict):
    r = client.get(f"{API}/analytics/summary", headers=admin)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["standard"]["motion_events"] == 0
    assert len(data["standard"]["hourly"]) >= 24 and all(b["value"] == 0 for b in data["standard"]["hourly"])
    assert len(data["standard"]["heatmap"]) == 7 * 24
    assert data["ai"]["available"] is False  # AI off → no people numbers, never zeros pretending to be data
    assert "limitations" in data


def test_standard_and_ai_statistics(client: TestClient, admin: dict):
    a = agent(client, admin)
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    evs = [
        event("cam-a", "motion_started", occurred_at=at(base, minutes=1)),
        event("cam-a", "motion_ended", occurred_at=at(base, minutes=2), metadata={"duration_s": 60}),
        event("cam-a", "motion_started", occurred_at=at(base, hours=1, minutes=5)),
        event("cam-b", "motion_started", occurred_at=at(base, hours=1, minutes=6)),
        event("cam-a", "object_entered_zone", zone_id="z1", occurred_at=at(base, minutes=1),
              metadata={"zone_name": "Door"}),
        event("cam-a", "object_exited_zone", zone_id="z1", occurred_at=at(base, minutes=3),
              metadata={"zone_name": "Door", "dwell_s": 120}),
        event("cam-a", "line_crossed", line_id="l1", occurred_at=at(base, minutes=1),
              metadata={"direction": "forward", "line_name": "Gate"}),
        # people: two tracks on cam-a (one seen many times), one crossing in, one out
        person("cam-a", "t1", occurred_at=at(base, minutes=10)),
        person("cam-a", "t1", "person_detected", occurred_at=at(base, minutes=11)),
        person("cam-a", "t1", "person_crossed_line", line_id="l1", direction="forward",
               occurred_at=at(base, minutes=12)),
        person("cam-a", "t1", "person_left", duration_s=30, occurred_at=at(base, minutes=13)),
        person("cam-a", "t2", occurred_at=at(base, hours=1, minutes=10)),
        person("cam-a", "t2", "person_crossed_line", line_id="l1", direction="backward",
               occurred_at=at(base, hours=1, minutes=11)),
    ]  # fmt: skip
    up = a.upload(evs).json()
    assert len(up["accepted"]) == len(evs), up["rejected"]
    # re-sending the same batch must not change any number (duplicates are ignored)
    assert len(a.upload(evs).json()["duplicate"]) == len(evs)

    params = {"since": base.isoformat(), "until": (base + timedelta(hours=4)).isoformat()}
    data = client.get(f"{API}/analytics/summary", params=params, headers=admin).json()
    std, ai = data["standard"], data["ai"]
    assert std["motion_events"] == 3 and std["activity_duration_s"] == 60
    assert sum(b["value"] for b in std["hourly"]) == 3
    assert {c["camera_ref"]: c["motion_events"] for c in std["cameras"]} == {"cam-a": 2, "cam-b": 1}
    zone = std["zones"][0]
    assert (zone["entries"], zone["exits"], zone["occupancy_estimate"], zone["avg_dwell_s"]) == (1, 1, 0, 120)
    assert std["lines"][0]["forward"] == 1 and std["lines"][0]["backward"] == 0
    # standard analytics never count people
    assert "people_observations" not in std
    assert ai["available"] is True
    assert ai["people_observations"] == 2  # one observation per temporary track, not per event
    assert (ai["inbound"], ai["outbound"]) == (1, 1)
    assert ai["avg_observation_duration_s"] == 30
    assert sum(b["value"] for b in ai["time_distribution"]) == 2

    # camera filter
    only_b = client.get(f"{API}/analytics/summary", params=params | {"camera_ref": "cam-b"}, headers=admin).json()
    assert only_b["standard"]["motion_events"] == 1 and only_b["ai"]["available"] is False


def test_time_zone_bucketing_and_validation(client: TestClient, admin: dict):
    a = agent(client, admin)
    t = datetime(2026, 3, 10, 22, 30, tzinfo=UTC)  # 03:30 next day in Asia/Tashkent (UTC+5)
    a.upload([event("cam-a", "motion_started", occurred_at=t.isoformat())])
    params = {"since": "2026-03-10T00:00:00Z", "until": "2026-03-12T00:00:00Z"}
    utc = client.get(f"{API}/analytics/summary", params=params | {"tz": "UTC"}, headers=admin).json()
    tash = client.get(f"{API}/analytics/summary", params=params | {"tz": "Asia/Tashkent"}, headers=admin).json()
    hit = lambda d: [b["bucket"] for b in d["standard"]["daily"] if b["value"]]  # noqa: E731
    assert hit(utc) == ["2026-03-10"] and hit(tash) == ["2026-03-11"]
    tash_hour = [b["bucket"] for b in tash["standard"]["hourly"] if b["value"]]
    assert tash_hour == ["2026-03-11 03:00"]
    bad = client.get(f"{API}/analytics/summary", params={"tz": "Mars/Base"}, headers=admin)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_timezone"
    rev = client.get(f"{API}/analytics/summary", params={"since": params["until"], "until": params["since"]},
                     headers=admin)  # fmt: skip
    assert rev.json()["error"]["code"] == "invalid_range"


def test_dst_day_has_23_hours():
    q = service.make_query(datetime(2026, 3, 8, 5, tzinfo=UTC), datetime(2026, 3, 9, 4, tzinfo=UTC), "America/New_York")
    hours = service._hour_series(q, {})
    assert len(hours) == 23  # spring-forward: 02:00 local does not exist


def test_people_analytics_hidden_without_permission(client: TestClient, admin: dict, viewer: dict):
    a = agent(client, admin)
    a.upload([person("cam-a", "t1")])
    assert client.get(f"{API}/analytics/summary", headers=admin).json()["ai"]["available"] is True
    hidden = client.get(f"{API}/analytics/summary", headers=viewer).json()["ai"]
    assert hidden["available"] is False and "observations:read" in hidden["reason"]


@pytest.mark.parametrize("kind", ["daily", "weekly", "monthly", "camera", "device", "zone", "ai"])
def test_reports_json_csv_pdf(client: TestClient, admin: dict, kind: str):
    a = agent(client, admin)
    a.upload([event("cam-a", "motion_started"), person("cam-a", "t1")])
    params = {"device_id": a.device_id, "camera_ref": "cam-a"} if kind in ("camera", "device") else {}
    js = client.get(f"{API}/analytics/reports/{kind}", params=params, headers=admin)
    assert js.status_code == 200, js.text
    csv_r = client.get(f"{API}/analytics/reports/{kind}", params=params | {"format": "csv"}, headers=admin)
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
    assert f"{kind}-report-" in csv_r.headers["content-disposition"] and len(csv_r.text) > 20
    pdf = client.get(f"{API}/analytics/reports/{kind}", params=params | {"format": "pdf"}, headers=admin)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"


def test_unknown_report_kind(client: TestClient, admin: dict):
    assert client.get(f"{API}/analytics/reports/yearly", headers=admin).status_code in (400, 404, 422)


def test_ai_performance_from_heartbeats(client: TestClient, admin: dict):
    a = agent(client, admin)
    a.heartbeat(ai={"enabled": True, "mode": "active", "model": "yolox-nano", "inference_fps": 4.0,
                    "latency_avg_ms": 120.0, "latency_p95_ms": 200.0})  # fmt: skip
    data = client.get(f"{API}/analytics/ai-performance", headers=admin).json()
    dev = data["devices"][0]
    assert dev["max_latency_ms"] is not None and data["heartbeats"] >= 2


# ---------------------------------------------------------------------------- alert rules API


def test_rule_crud_validation_and_permissions(client: TestClient, admin: dict, viewer: dict):
    cat = client.get(f"{API}/alert-rules/catalog", headers=admin).json()
    assert {k["kind"] for k in cat["kinds"]} >= {"camera_offline", "high_cpu", "line_crossed"}
    assert cat["email"]["configured"] is False
    body = {"name": "Motion at gate", "kind": "motion_detected", "camera_ref": "cam-a"}
    assert client.post(f"{API}/alert-rules", json=body, headers=viewer).status_code == 403
    r = client.post(f"{API}/alert-rules", json=body, headers=admin)
    assert r.status_code == 201 and r.json()["severity"] == "info" and r.json()["channels"] == ["in_app"]
    rid = r.json()["id"]
    bad = client.post(f"{API}/alert-rules", json=body | {"channels": ["email"]}, headers=admin)
    assert bad.json()["error"]["code"] == "recipients_required"
    assert (
        client.post(f"{API}/alert-rules", json=body | {"email_recipients": ["nope"]}, headers=admin).status_code == 422
    )
    assert client.post(f"{API}/alert-rules", json=body | {"kind": "fire"}, headers=admin).status_code == 422
    upd = client.put(f"{API}/alert-rules/{rid}", json=body | {"enabled": False, "cooldown_s": 60}, headers=admin)
    assert upd.json()["enabled"] is False and upd.json()["cooldown_s"] == 60
    assert len(client.get(f"{API}/alert-rules", headers=viewer).json()) == 1
    rec = client.post(f"{API}/alert-rules/recommended", headers=admin).json()
    assert len(rec) == 11 and all(x["channels"] == ["in_app"] for x in rec)
    assert client.post(f"{API}/alert-rules/recommended", headers=admin).json() == []  # idempotent
    assert client.delete(f"{API}/alert-rules/{rid}", headers=admin).status_code == 204
    r = client.post(f"{API}/alert-rules/test-email", json={"to": "ops@example.com"}, headers=admin)
    assert r.json()["error"]["code"] == "smtp_not_configured"


# ---------------------------------------------------------------------------- alert engine


def rule(client, admin, **body) -> str:
    r = client.post(f"{API}/alert-rules", json={"name": body.get("kind", "rule")} | body, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_motion_rule_fires_with_scope_and_cooldown(client: TestClient, admin: dict, db: Session):
    a = agent(client, admin)
    rule(client, admin, kind="motion_detected", camera_ref="cam-a", cooldown_s=300)
    a.upload([event("cam-b", "motion_started")])  # other camera → out of scope
    assert client.get(f"{API}/alerts", headers=admin).json() == []
    a.upload([event("cam-a", "motion_started"), event("cam-a", "motion_started")])
    alerts = client.get(f"{API}/alerts", headers=admin).json()
    assert len(alerts) == 1  # cooldown: one alert per rule + scope per period
    al = alerts[0]
    assert al["kind"] == "motion_detected" and al["camera_ref"] == "cam-a" and "Gate" in al["title"]
    assert al["status"] == "open"
    # cooldown passed → fires again
    db.query(Alert).update({Alert.created_at: datetime.now(UTC) - timedelta(seconds=301)})
    db.commit()
    a.upload([event("cam-a", "motion_started")])
    assert len(client.get(f"{API}/alerts", headers=admin).json()) == 2
    assert client.get(f"{API}/alerts/counts", headers=admin).json()["open"] == 2


def test_disabled_rule_and_no_rules_fire_nothing(client: TestClient, admin: dict):
    a = agent(client, admin)
    a.upload([event("cam-a", "motion_started")])
    rule(client, admin, kind="motion_detected", enabled=False)
    a.upload([event("cam-a", "motion_started")])
    assert client.get(f"{API}/alerts", headers=admin).json() == []


def test_camera_offline_vs_stream_error_and_reconnect_resolves(client: TestClient, admin: dict):
    a = agent(client, admin)
    for k in ("camera_offline", "stream_error", "camera_reconnected"):
        rule(client, admin, kind=k, cooldown_s=0)
    a.upload([event("cam-a", "camera_error", source="system", metadata={"failure_kind": "unreachable"})])
    a.upload([event("cam-b", "camera_error", source="system", metadata={"failure_kind": "decode", "error": "bad"})])
    kinds = {x["camera_ref"]: x["kind"] for x in client.get(f"{API}/alerts", headers=admin).json()}
    assert kinds == {"cam-a": "camera_offline", "cam-b": "stream_error"}
    a.upload([event("cam-a", "camera_recovered", source="system")])
    by = {(x["camera_ref"], x["kind"]): x["status"] for x in client.get(f"{API}/alerts", headers=admin).json()}
    assert by[("cam-a", "camera_offline")] == "resolved"
    assert by[("cam-b", "stream_error")] == "open"  # other camera untouched
    assert by[("cam-a", "camera_reconnected")] == "open"


def test_ai_offline_and_model_error(client: TestClient, admin: dict):
    a = agent(client, admin)
    rule(client, admin, kind="ai_offline")
    rule(client, admin, kind="model_error")
    a.upload([event("cam-a", "ai_error", source="system", camera_id="",
                    metadata={"mode": "degraded", "reason": "Model file missing"})])  # fmt: skip
    assert [x["kind"] for x in client.get(f"{API}/alerts", headers=admin).json()] == ["model_error"]
    a.upload([event("cam-a", "ai_error", source="system", camera_id="",
                    metadata={"mode": "degraded", "reason": "inference too slow"})])  # fmt: skip
    assert {x["kind"] for x in client.get(f"{API}/alerts", headers=admin).json()} == {"model_error", "ai_offline"}


def test_metric_rules_on_heartbeat(client: TestClient, admin: dict):
    a = agent(client, admin)
    rule(client, admin, kind="high_cpu", threshold=80)
    rule(client, admin, kind="low_disk")  # default 5 GB free
    rule(client, admin, kind="high_temperature")
    a.heartbeat(metrics={"cpu": {"percent": 50}, "disk": {"free_gb": 100},
                         "temperature": {"available": False}})  # fmt: skip
    assert client.get(f"{API}/alerts", headers=admin).json() == []
    a.heartbeat(metrics={"cpu": {"percent": 95.5}, "disk": {"free_gb": 2},
                         "temperature": {"available": True, "max_c": 90}})  # fmt: skip
    alerts = {x["kind"]: x for x in client.get(f"{API}/alerts", headers=admin).json()}
    assert set(alerts) == {"high_cpu", "low_disk", "high_temperature"}
    assert alerts["high_cpu"]["context"]["value"] == 95.5 and alerts["high_cpu"]["context"]["threshold"] == 80
    a.heartbeat(metrics={"cpu": {"percent": 99}})  # still high, within cooldown → no duplicate
    assert len(client.get(f"{API}/alerts", headers=admin).json()) == 3


def test_device_offline_alert_from_monitor(client: TestClient, admin: dict, db: Session, settings):
    a = agent(client, admin)
    rule(client, admin, kind="device_offline")
    from app.models import EdgeDevice

    dev = db.get(EdgeDevice, uuid.UUID(a.device_id))
    dev.last_heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    mark_stale_devices(db, settings)
    db.commit()
    alerts = client.get(f"{API}/alerts", headers=admin).json()
    assert [x["kind"] for x in alerts] == ["device_offline"] and alerts[0]["severity"] == "critical"


def test_acknowledge_resolve_and_permissions(client: TestClient, admin: dict, viewer: dict):
    a = agent(client, admin)
    rule(client, admin, kind="motion_detected", cooldown_s=0)
    a.upload([event("cam-a", "motion_started"), event("cam-b", "motion_started")])
    ids = [x["id"] for x in client.get(f"{API}/alerts", headers=admin).json()]
    assert len(ids) == 2
    assert client.post(f"{API}/alerts/{ids[0]}/acknowledge", headers=viewer).status_code == 403
    ack = client.post(f"{API}/alerts/{ids[0]}/acknowledge", headers=admin).json()
    assert ack["status"] == "acknowledged" and ack["acknowledged_at"]
    res = client.post(f"{API}/alerts/{ids[0]}/resolve", headers=admin).json()
    assert res["status"] == "resolved"
    assert client.post(f"{API}/alerts/acknowledge-all", headers=admin).json() == {"acknowledged": 1}
    active = client.get(f"{API}/alerts", params={"status": "active"}, headers=admin).json()
    assert [x["status"] for x in active] == ["acknowledged"]
    assert client.post(f"{API}/alerts/{uuid.uuid4()}/acknowledge", headers=admin).status_code == 404
    dl = client.get(f"{API}/alerts/{ids[0]}/deliveries", headers=admin).json()
    assert dl == [{"channel": "in_app", "recipient": None, "status": "sent", "attempts": 1, "error": None,
                   "sent_at": dl[0]["sent_at"]}]  # fmt: skip


def test_email_skipped_when_smtp_not_configured(client: TestClient, admin: dict, db: Session):
    a = agent(client, admin)
    rule(client, admin, kind="motion_detected", channels=["in_app", "email"], email_recipients=["Ops@Example.com"])
    a.upload([event("cam-a", "motion_started")])
    al = client.get(f"{API}/alerts", headers=admin).json()[0]
    dl = {d["channel"]: d for d in client.get(f"{API}/alerts/{al['id']}/deliveries", headers=admin).json()}
    assert dl["email"]["status"] == "skipped" and "SMTP" in dl["email"]["error"]
    assert dl["email"]["recipient"] == "ops@example.com"


def test_notifier_delivers_and_retries(client: TestClient, admin: dict, db: Session, settings):
    settings.smtp_host, settings.smtp_from = "smtp.example.test", "aci@example.test"
    a = agent(client, admin)
    rule(client, admin, kind="motion_detected", channels=["email"], email_recipients=["a@example.com", "b@example.com"])
    a.upload([event("cam-a", "motion_started")])
    alert_id = db.scalar(select(Alert.id))
    pending = list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.alert_id == alert_id)))
    assert {d.status for d in pending} == {"pending"} and len(pending) == 2

    sent: list[tuple] = []
    failures = {"b@example.com": 1}  # b fails once, then succeeds

    def fake_send(_settings, to, subject, body):
        if failures.get(to):
            failures[to] -= 1
            raise ConnectionError("temporary")
        sent.append((to, subject, body))

    notifier = Notifier(settings, session_scope, sender=fake_send, retry_delays=(0, 0))
    assert sorted(notifier.deliver_alert(alert_id)) == ["sent", "sent"]
    assert {s[0] for s in sent} == {"a@example.com", "b@example.com"}
    assert sent[0][1].startswith("[INFO] Motion detected")
    db.expire_all()
    rows = {d.recipient: d for d in db.scalars(select(NotificationDelivery))}
    assert rows["b@example.com"].attempts == 2 and rows["b@example.com"].status == "sent"
    assert rows["a@example.com"].attempts == 1

    # permanent failure → failed after all attempts, never raises
    def always_fail(*_a):
        raise OSError("no route")

    a.upload([event("cam-b", "motion_started")])
    second = db.scalar(select(Alert.id).where(Alert.id != alert_id))
    broken = Notifier(settings, session_scope, sender=always_fail, retry_delays=(0, 0))
    assert broken.deliver_alert(second) == ["failed", "failed"]
    db.expire_all()
    failed = list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.alert_id == second)))
    assert all(d.status == "failed" and d.attempts == 3 and "no route" in d.error for d in failed)


def test_alerts_are_published_live_after_commit(client: TestClient, admin: dict):
    a = agent(client, admin)
    rule(client, admin, kind="motion_detected")
    token = admin["Authorization"].split()[1]
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "welcome"
        a.upload([event("cam-a", "motion_started")])
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "alert.created":
                assert msg["data"]["kind"] == "motion_detected" and "email_pending" in msg["data"]
                break
        else:
            pytest.fail("alert.created not received")


def test_alert_failure_never_breaks_ingestion(client: TestClient, admin: dict, monkeypatch):
    from app.alerts import runtime

    a = agent(client, admin)
    rule(client, admin, kind="motion_detected")

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(runtime.engine, "on_events", boom)
    r = a.upload([event("cam-a", "motion_started")])
    assert r.status_code == 200 and len(r.json()["accepted"]) == 1
