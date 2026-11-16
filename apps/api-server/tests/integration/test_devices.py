"""MVP 06 — central server: device lifecycle, event ingestion, configuration, real-time updates."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.ratelimit import limiter
from app.models import AuditLog, Camera, CameraEvent, EdgeDevice, EventStatistic, SynchronizationLog
from app.services.auth import create_user
from app.services.devices import mark_stale_devices

PASSWORD = "a-strong-test-password"


@pytest.fixture(autouse=True)
def _fresh_limiter():
    limiter._buckets.clear()
    yield


@pytest.fixture
def admin(client: TestClient, db: Session) -> dict:
    create_user(db, email="admin@example.com", password=PASSWORD, role_name="administrator", full_name=None)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


@pytest.fixture
def viewer(client: TestClient, db: Session) -> dict:
    create_user(db, email="viewer@example.com", password=PASSWORD, role_name="viewer", full_name=None)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": "viewer@example.com", "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def enroll(client: TestClient, admin: dict, **body) -> str:
    r = client.post("/api/v1/devices/enrollment-tokens", json=body, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()["token"]


class Agent:
    """Minimal device-side client (what the edge agent sends)."""

    def __init__(self, client: TestClient, name: str = "edge") -> None:
        self.client = client
        self.device_id = str(uuid.uuid4())
        self.name = name
        self.secret: str | None = None

    def register(self, token: str):
        r = self.client.post(
            "/api/v1/devices/register",
            json={"device_id": self.device_id, "device_name": self.name, "agent_version": "0.6.0",
                  "hardware": {"architecture": "x86_64"}},
            headers={"Authorization": f"Bearer {token}"},
        )  # fmt: skip
        if r.status_code == 201:
            self.secret = r.json()["device_secret"]
        return r

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.secret}", "X-ACI-Device-ID": self.device_id}

    def heartbeat(self, **extra):
        body = {
            "device_id": self.device_id, "device_name": self.name, "agent_version": "0.6.0", "config_version": 0,
            "heartbeat_interval_s": 30, "metrics": {"cpu": {"percent": 5}, "disk": {"used_percent": 40}},
            "health": {"database_ok": True, "storage_paused": False},
            "ai": {"enabled": False, "mode": "disabled"},
            "cameras": {"total": 2, "by_status": {"online": 2}},
            "camera_list": [
                {"id": "cam-a", "name": "Gate", "source_type": "rtsp", "status": "online", "fps": 15.0},
                {"id": "cam-b", "name": "Yard", "source_type": "usb", "status": "online", "is_enabled": True},
            ],
            "sync": {"pending": 0, "dropped": 0},
        } | extra  # fmt: skip
        return self.client.post("/api/v1/devices/heartbeat", json=body, headers=self.headers)

    def upload(self, events: list[dict]):
        return self.client.post(
            "/api/v1/events/batch", json={"device_id": self.device_id, "events": events}, headers=self.headers
        )


def event(camera: str = "cam-a", type_: str = "motion_started", **kw) -> dict:
    return {"id": str(uuid.uuid4()), "camera_id": camera, "event_type": type_, "source": "standard",
            "occurred_at": datetime.now(UTC).isoformat(), "metadata": {"k": 1}} | kw  # fmt: skip


# ---------------------------------------------------------------------------- registration / auth


def test_register_heartbeat_and_list(client: TestClient, admin: dict, db: Session):
    agent = Agent(client, "Gate box")
    assert agent.register(enroll(client, admin)).status_code == 201
    r = agent.heartbeat()
    assert r.status_code == 200 and r.json()["status"] == "online"
    devices = client.get("/api/v1/devices", headers=admin).json()
    assert [d["name"] for d in devices] == ["Gate box"]
    detail = client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).json()
    assert detail["status"] == "online" and detail["agent_version"] == "0.6.0"
    assert {c["external_id"] for c in detail["cameras"]} == {"cam-a", "cam-b"}
    assert detail["camera_status"]["by_status"] == {"online": 2}
    assert "device_secret" not in str(detail) and agent.secret not in str(detail)
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "device.registered")) == 1


def test_enrollment_token_is_single_use_and_expires(client: TestClient, admin: dict):
    token = enroll(client, admin)
    assert Agent(client).register(token).status_code == 201
    r = Agent(client).register(token)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_enrollment_token"
    assert Agent(client).register("aci_enr_wrong_token_value_123456").status_code == 401
    multi = enroll(client, admin, max_uses=3)
    assert all(Agent(client).register(multi).status_code == 201 for _ in range(3))


def test_device_authentication(client: TestClient, admin: dict):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    other = Agent(client)
    other.register(enroll(client, admin))
    bad = {"Authorization": "Bearer aci_dev_not_a_real_secret_123456", "X-ACI-Device-ID": agent.device_id}
    assert client.post("/api/v1/devices/heartbeat", json={}, headers=bad).status_code == 401
    # another device's secret with my id
    stolen = {"Authorization": f"Bearer {other.secret}", "X-ACI-Device-ID": agent.device_id}
    assert client.post("/api/v1/devices/heartbeat", json={}, headers=stolen).status_code == 401
    # a device can't read another device's configuration
    r = client.get(f"/api/v1/devices/{other.device_id}/configuration", headers=agent.headers)
    assert r.status_code == 403
    # user tokens can't act as devices and vice versa
    assert client.post("/api/v1/events/batch", json={"events": []}, headers=admin).status_code == 401
    assert client.get("/api/v1/devices", headers=agent.headers).status_code == 401


def test_rbac(client: TestClient, admin: dict, viewer: dict):
    assert client.get("/api/v1/devices", headers=viewer).status_code == 200
    assert client.post("/api/v1/devices/enrollment-tokens", json={}, headers=viewer).status_code == 403
    agent = Agent(client)
    agent.register(enroll(client, admin))
    assert client.post(f"/api/v1/devices/{agent.device_id}/revoke", headers=viewer).status_code == 403
    r = client.patch(f"/api/v1/devices/{agent.device_id}/configuration", json={"base_version": 0, "config": {}},
                     headers=viewer)  # fmt: skip
    assert r.status_code == 403
    assert client.get("/api/v1/devices").status_code == 401


# ---------------------------------------------------------------------------- events


def test_event_batch_idempotent_and_statistics(client: TestClient, admin: dict, db: Session):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    agent.heartbeat()
    events = [event() for _ in range(5)] + [event("cam-b", "line_crossed", line_id="l1")]
    r = agent.upload(events).json()
    assert len(r["accepted"]) == 6 and not r["duplicate"] and not r["rejected"]
    # repeat (lost acknowledgement) + one new
    again = agent.upload(events + [event()]).json()
    assert len(again["duplicate"]) == 6 and len(again["accepted"]) == 1
    assert db.scalar(select(func.count()).select_from(CameraEvent)) == 7
    stats = client.get("/api/v1/events/statistics/summary", headers=admin).json()["counts"]
    assert stats == {"motion_started": 6, "line_crossed": 1}  # repeats never double count
    stored = db.get(CameraEvent, uuid.UUID(events[0]["id"]))
    cam = db.scalar(select(Camera).where(Camera.external_id == "cam-a"))
    assert stored.camera_id == cam.id and stored.camera_ref == "cam-a" and str(stored.device_id) == agent.device_id
    logs = db.scalars(select(SynchronizationLog).where(SynchronizationLog.kind == "events")).all()
    assert [(log.accepted, log.duplicate) for log in logs] == [(6, 0), (1, 6)]


def test_invalid_events_rejected_individually(client: TestClient, admin: dict):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    good = event()
    batch = [
        good,
        event(type_="teleported"),
        event(occurred_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat()),
        event(occurred_at="2026-11-16T10:00:00"),  # no timezone
        {"id": "not-a-uuid", "camera_id": "x", "event_type": "motion_started", "occurred_at": "2026-11-16T10:00:00Z"},
        event(metadata={"blob": "x" * 20_000}),
        "garbage",
    ]
    r = agent.upload(batch).json()
    assert r["accepted"] == [good["id"]]
    assert len(r["rejected"]) == 6
    assert all(item["reason"] for item in r["rejected"])


def test_event_id_of_another_device_is_rejected(client: TestClient, admin: dict):
    a, b = Agent(client, "a"), Agent(client, "b")
    a.register(enroll(client, admin))
    b.register(enroll(client, admin))
    e = event()
    assert a.upload([e]).json()["accepted"] == [e["id"]]
    r = b.upload([e]).json()
    assert r["rejected"][0]["reason"].startswith("event id already used")


def test_concurrent_uploads_from_several_devices(client: TestClient, admin: dict, db: Session):
    agents = [Agent(client, f"edge-{i}") for i in range(4)]
    token = enroll(client, admin, max_uses=4)
    for a in agents:
        assert a.register(token).status_code == 201
    batches = {a.device_id: [event() for _ in range(50)] for a in agents}
    errors = []

    def send(a: Agent):
        for chunk in range(0, 50, 10):
            # every chunk twice: concurrent duplicates must be stored once
            for _ in range(2):
                r = a.upload(batches[a.device_id][chunk : chunk + 10])
                if r.status_code != 200:
                    errors.append(r.text)

    threads = [threading.Thread(target=send, args=(a,)) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert db.scalar(select(func.count()).select_from(CameraEvent)) == 200
    assert db.scalar(select(func.sum(EventStatistic.count))) == 200


def test_event_queries_and_pagination(client: TestClient, admin: dict, viewer: dict):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    base = datetime.now(UTC) - timedelta(minutes=30)
    events = [event(occurred_at=(base + timedelta(seconds=i)).isoformat()) for i in range(7)]
    agent.upload(events)
    page = client.get("/api/v1/events?limit=3", headers=viewer).json()
    seen = [e["id"] for e in page["items"]]
    while page["next_cursor"]:
        page = client.get(f"/api/v1/events?limit=3&cursor={page['next_cursor']}", headers=viewer).json()
        seen += [e["id"] for e in page["items"]]
    assert seen == [e["id"] for e in reversed(events)]
    one = client.get(f"/api/v1/events/{events[0]['id']}", headers=viewer).json()
    assert one["camera_ref"] == "cam-a" and one["metadata"] == {"k": 1}
    filtered = client.get(f"/api/v1/events?device_id={agent.device_id}&event_type=line_crossed", headers=viewer)
    assert filtered.json()["items"] == []


def test_rate_limit_returns_retry_after(client: TestClient, admin: dict, settings):
    settings.rate_events_per_s, settings.rate_events_burst = 0.2, 3
    agent = Agent(client)
    agent.register(enroll(client, admin))
    codes = [agent.upload([]).status_code for _ in range(6)]
    assert 429 in codes
    r = agent.upload([])
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------------------- status


def test_status_derivation_and_offline_monitor(client: TestClient, admin: dict, db: Session, settings):
    settings.rate_heartbeat_burst = 50  # this test sends many heartbeats back to back
    agent = Agent(client)
    agent.register(enroll(client, admin))
    assert client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).json()["status"] == "pending"
    assert agent.heartbeat(sync={"pending": 40}).json()["status"] == "synchronizing"
    assert agent.heartbeat(cameras={"total": 2, "by_status": {"online": 1, "error": 1}}).json()["status"] == "degraded"
    assert agent.heartbeat(ai={"enabled": True, "mode": "error"}).json()["status"] == "degraded"
    assert agent.heartbeat(health={"database_ok": False}).json()["status"] == "error"
    assert agent.heartbeat().json()["status"] == "online"
    device = db.get(EdgeDevice, uuid.UUID(agent.device_id))
    device.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    db.commit()
    assert mark_stale_devices(db, settings) == 1
    db.commit()
    detail = client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).json()
    assert detail["status"] == "offline" and "no heartbeat" in detail["status_reason"]
    assert agent.heartbeat().json()["status"] == "online"  # connection restored
    status = client.get("/api/v1/system/status").json()
    assert status["devices"]["by_status"] == {"online": 1}


def test_revocation_and_deletion(client: TestClient, admin: dict, db: Session):
    agent, other = Agent(client, "a"), Agent(client, "b")
    token = enroll(client, admin, max_uses=2)
    agent.register(token)
    other.register(token)
    agent.upload([event()])
    r = client.post(f"/api/v1/devices/{agent.device_id}/revoke", headers=admin)
    assert r.json()["status"] == "revoked"
    hb = agent.heartbeat()
    assert hb.status_code == 401 and hb.json()["error"]["code"] == "device_revoked"
    assert other.heartbeat().status_code == 200  # other devices unaffected
    # re-enrol with a new token works and invalidates the old secret for good
    old = agent.secret
    assert agent.register(enroll(client, admin)).status_code == 201 and agent.secret != old
    assert agent.heartbeat().status_code == 200
    assert client.delete(f"/api/v1/devices/{agent.device_id}", headers=admin).status_code == 204
    assert client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).status_code == 404
    assert db.scalar(select(func.count()).select_from(CameraEvent)) == 1  # events kept
    assert agent.heartbeat().status_code == 401


def test_credential_rotation(client: TestClient, admin: dict, db: Session):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    assert agent.heartbeat().json()["rotate_credentials"] is False
    client.post(f"/api/v1/devices/{agent.device_id}/rotate-credentials", headers=admin)
    assert agent.heartbeat().json()["rotate_credentials"] is True
    old_headers = dict(agent.headers)
    new_secret = client.post("/api/v1/devices/credentials/rotate", headers=agent.headers).json()["device_secret"]
    # old credential still valid until the new one is used (reply might have been lost)
    assert client.post("/api/v1/devices/heartbeat", json={}, headers=old_headers).status_code == 200
    agent.secret = new_secret
    assert agent.heartbeat().json()["rotate_credentials"] is False
    # first use of the new credential starts the grace period of the old one
    from app.models import DeviceCredential

    old = db.scalars(select(DeviceCredential).where(DeviceCredential.origin == "enrollment")).one()
    assert old.expires_at is not None
    old.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.post("/api/v1/devices/heartbeat", json={}, headers=old_headers).status_code == 401
    assert agent.heartbeat().status_code == 200


# ---------------------------------------------------------------------------- configuration


def test_configuration_versions_ack_conflict_and_rollback(client: TestClient, admin: dict):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    url = f"/api/v1/devices/{agent.device_id}/configuration"
    r = client.patch(url, json={"base_version": 0, "config": {"ai_enabled": True, "events": {"retention_days": 14},
                                                              "cameras": {"cam-a": {"motion": {"sensitivity": 80}}}}},
                     headers=admin)  # fmt: skip
    assert r.status_code == 200 and r.json()["version"] == 1
    assert agent.heartbeat().json()["config_version"] == 1
    doc = client.get(url, headers=agent.headers).json()
    assert doc["version"] == 1 and doc["config"]["events"] == {"retention_days": 14} and len(doc["checksum"]) == 64
    # second admin edits from a stale view → conflict
    stale = client.patch(url, json={"base_version": 0, "config": {"ai_enabled": False}}, headers=admin)
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current_version"] == 1
    # merge patch: null removes, nested merge keeps siblings
    v2 = client.patch(url, json={"base_version": 1, "config": {"ai_enabled": None,
                                                               "cameras": {"cam-a": {"motion": {"enabled": False}}}}},
                      headers=admin).json()  # fmt: skip
    assert v2["version"] == 2 and "ai_enabled" not in v2["config"]
    assert v2["config"]["cameras"]["cam-a"]["motion"] == {"sensitivity": 80, "enabled": False}
    # device acknowledges
    ack = client.post(f"{url}/ack", json={"version": 2, "status": "applied", "applied": ["cameras", "events"]},
                      headers=agent.headers)  # fmt: skip
    assert ack.status_code == 200
    state = client.get(url, headers=admin).json()
    assert state["status"] == "applied" and state["applied_version"] == 2
    # rollback to v1 = new version 3 with v1 content
    rb = client.post(f"{url}/rollback", json={"to_version": 1, "base_version": 2}, headers=admin).json()
    assert rb["version"] == 3 and rb["config"]["ai_enabled"] is True
    history = client.get(f"{url}/history", headers=admin).json()
    assert [h["version"] for h in history] == [3, 2, 1] and history[0]["rollback_of"] == 1
    assert history[2]["status"] == "superseded"  # v1 never acknowledged before v2 arrived
    # failed apply is visible and degrades the device status
    client.post(f"{url}/ack", json={"version": 3, "status": "failed", "errors": {"cameras": "cam-a: not found"}},
                headers=agent.headers)  # fmt: skip
    detail = client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).json()
    assert detail["status"] == "degraded" and "v3 failed" in detail["status_reason"]


def test_invalid_configuration_is_rejected(client: TestClient, admin: dict):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    url = f"/api/v1/devices/{agent.device_id}/configuration"
    for bad in (
        {"ai_enabled": "yes please"},
        {"events": {"retention_days": 0}},
        {"cameras": {"cam-a": {"motion": {"sensitivity": 500}}}},
        {"cameras": {"cam-a": {"zones": [{"name": "z", "polygon": [[0, 0], [2, 0], [0, 1]]}]}}},
        {"shell_command": "rm -rf /"},
    ):
        r = client.patch(url, json={"base_version": 0, "config": bad}, headers=admin)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_configuration", bad


def test_device_groups(client: TestClient, admin: dict):
    g = client.post("/api/v1/device-groups", json={"name": "Warehouse"}, headers=admin).json()
    assert client.post("/api/v1/device-groups", json={"name": "Warehouse"}, headers=admin).status_code == 409
    agent = Agent(client)
    agent.register(enroll(client, admin, group_id=g["id"]))
    d = client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).json()
    assert d["group_id"] == g["id"] and d["group_name"] == "Warehouse"
    assert client.get("/api/v1/device-groups", headers=admin).json()[0]["device_count"] == 1
    assert len(client.get(f"/api/v1/devices?group_id={g['id']}", headers=admin).json()) == 1
    client.patch(f"/api/v1/devices/{agent.device_id}", json={"name": "Dock 1", "group_id": None}, headers=admin)
    assert client.get(f"/api/v1/devices/{agent.device_id}", headers=admin).json()["group_id"] is None
    assert client.delete(f"/api/v1/device-groups/{g['id']}", headers=admin).status_code == 204


# ---------------------------------------------------------------------------- WebSocket


def test_websocket_requires_auth_and_streams_updates(client: TestClient, admin: dict):
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        assert ws.receive_json()["type"] == "error"
    token = admin["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "welcome"
        agent = Agent(client, "Live")
        agent.register(enroll(client, admin))
        agent.heartbeat()
        agent.upload([event()])
        seen = set()
        for _ in range(12):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if {"device.status", "event.created", "sync.status", "camera.status", "system.notification"} <= seen:
                break
        assert {"device.status", "event.created", "sync.status", "camera.status", "system.notification"} <= seen
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_websocket_filters_by_permission(client: TestClient, admin: dict, db: Session):
    create_user(db, email="ops@example.com", password=PASSWORD, role_name="operator", full_name=None)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": "ops@example.com", "password": PASSWORD}).json()
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": token["access_token"]})
        assert ws.receive_json()["type"] == "welcome"
        agent = Agent(client)
        agent.register(enroll(client, admin))
        agent.upload([event()])
        msg = ws.receive_json()
        assert msg["type"] in ("device.status", "system.notification", "event.created", "sync.status")
