"""MVP 07 server APIs: users / roles, settings (retention, privacy), events search + CSV,
observations, dashboard overview, device health events."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CameraEvent, EdgeDevice
from app.services.devices import mark_stale_devices
from app.services.system_settings import apply_retention
from tests.integration.conftest import PASSWORD
from tests.integration.test_devices import Agent, enroll, event


def login(client: TestClient, email: str) -> dict:
    token = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def person(camera: str, track: str, type_: str = "person_detected", line_id: str | None = None, **meta) -> dict:
    base = {"confidence": 0.8, "heading": "left", "duration_s": 3.0, "detector": "yolox"}
    extra = {"occurred_at": meta.pop("occurred_at")} if "occurred_at" in meta else {}
    return event(camera, type_, source="ai", track_id=track, line_id=line_id, metadata=base | meta, **extra)


# ---------------------------------------------------------------------------- users & roles


def test_user_management_and_guards(client: TestClient, admin: dict, viewer: dict):
    body = {"email": "Op@Site.local", "password": "operator-pass-123", "role": "operator"}
    r = client.post("/api/v1/users", json=body, headers=admin)
    assert r.status_code == 201 and r.json()["email"] == "op@site.local" and r.json()["role"] == "operator"
    op_id = r.json()["id"]
    assert client.post("/api/v1/users", json={"email": "x@y", "password": "short", "role": "viewer"},
                       headers=admin).status_code == 422  # fmt: skip
    assert client.get("/api/v1/users", headers=viewer).status_code == 403
    users = client.get("/api/v1/users", headers=admin).json()
    assert {u["email"] for u in users} >= {"admin@example.com", "viewer@example.com", "op@site.local"}
    # deactivate + reactivate, password reset
    assert client.patch(f"/api/v1/users/{op_id}", json={"is_active": False}, headers=admin).json()["is_active"] is False
    assert (
        client.post("/api/v1/auth/login", json={"email": "op@site.local", "password": "operator-pass-123"}).status_code
        == 401
    )
    client.patch(f"/api/v1/users/{op_id}", json={"is_active": True, "password": "new-operator-pass-1"}, headers=admin)
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "op@site.local", "password": "new-operator-pass-1"}
        ).status_code
        == 200
    )
    # the last administrator can't lock themselves out
    me = next(u for u in users if u["email"] == "admin@example.com")
    r = client.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"}, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_administrator"
    assert client.delete(f"/api/v1/users/{me['id']}", headers=admin).status_code == 400
    assert client.delete(f"/api/v1/users/{op_id}", headers=admin).status_code == 204


def test_role_permissions_are_editable_except_administrator(client: TestClient, admin: dict, viewer: dict, db: Session):
    catalog = client.get("/api/v1/roles/permissions", headers=admin).json()
    assert "observations:read" in catalog and "users:manage" in catalog
    roles = {r["name"]: r for r in client.get("/api/v1/roles", headers=admin).json()}
    assert roles["administrator"]["editable"] is False and roles["operator"]["permissions"]
    assert "observations:read" in roles["operator"]["permissions"]  # migration 0003
    assert "observations:read" not in roles["viewer"]["permissions"]  # privacy default
    assert client.put("/api/v1/roles/administrator", json={"permissions": []}, headers=admin).status_code == 400
    assert client.put("/api/v1/roles/viewer", json={"permissions": ["fly"]}, headers=admin).status_code == 400
    assert client.put("/api/v1/roles/viewer", json={"permissions": ["users:manage"]}, headers=admin).status_code == 400
    # grant viewers observations → takes effect immediately on the server
    assert client.get("/api/v1/observations", headers=viewer).status_code == 403
    r = client.put("/api/v1/roles/viewer", json={"permissions": ["cameras:read", "events:read", "devices:read",
                                                                 "observations:read"]}, headers=admin)  # fmt: skip
    assert r.status_code == 200
    assert client.get("/api/v1/observations", headers=viewer).status_code == 200


# ---------------------------------------------------------------------------- settings, privacy, retention


def test_settings_privacy_and_retention(client: TestClient, admin: dict, viewer: dict, db: Session):
    assert client.get("/api/v1/system/settings", headers=viewer).json()["store_person_attributes"] is True
    assert client.patch("/api/v1/system/settings", json={"events_retention_days": 5}, headers=viewer).status_code == 403
    r = client.patch("/api/v1/system/settings", json={"store_person_attributes": False, "events_retention_days": 30,
                                                       "observations_retention_days": 7}, headers=admin)  # fmt: skip
    assert r.status_code == 200 and r.json()["events_retention_days"] == 30
    assert client.patch("/api/v1/system/settings", json={"events_retention_days": -1}, headers=admin).status_code == 422
    agent = Agent(client)
    agent.register(enroll(client, admin))
    p = person("cam-a", "t1", attributes={"upper_color": {"value": "red", "confidence": 0.9}})
    agent.upload([p])
    stored = db.get(CameraEvent, uuid.UUID(p["id"]))
    assert "attributes" not in stored.event_metadata and stored.event_metadata["confidence"] == 0.8
    # retention: old person events go after 7 days, other events after 30
    now = datetime.now(UTC)
    old_person = person("cam-a", "t2", occurred_at=(now - timedelta(days=10)).isoformat())
    old_motion = event(occurred_at=(now - timedelta(days=10)).isoformat())
    ancient = event(occurred_at=(now - timedelta(days=40)).isoformat())
    agent.upload([old_person, old_motion, ancient])
    report = apply_retention(db)
    db.commit()
    assert report == {"observations": 1, "events": 1}
    assert db.get(CameraEvent, uuid.UUID(old_motion["id"])) is not None
    cfg = client.get("/api/v1/system/configuration", headers=viewer).json()
    assert cfg["devices"]["heartbeat_interval_s"] == 30 and "jwt" not in str(cfg).lower()


# ---------------------------------------------------------------------------- events search / categories / CSV


def test_event_search_categories_and_csv(client: TestClient, admin: dict, viewer: dict, db: Session):
    agent = Agent(client, "Dock")
    agent.register(enroll(client, admin))
    agent.heartbeat()
    agent.upload([
        event("cam-a", "motion_started"), event("cam-a", "object_entered_zone", zone_id="loading-bay"),
        event("cam-b", "line_crossed", line_id="north-exit"), person("cam-a", "p1"),
        event("cam-a", "camera_error", source="system", metadata={"failure_kind": "unreachable"}),
        event("cam-a", "ai_error", source="system", metadata={"reason": "model missing"}),
    ])  # fmt: skip

    def types(params: str, headers=viewer):
        return sorted(e["event_type"] for e in client.get(f"/api/v1/events?{params}", headers=headers).json()["items"])

    assert types("category=motion") == ["motion_started"]
    assert types("category=zone") == ["object_entered_zone"]
    assert types("category=line") == ["line_crossed"]
    assert types("category=person") == ["person_detected"]
    assert types("category=camera_error&category=ai_error") == ["ai_error", "camera_error"]
    assert types("q=loading") == ["object_entered_zone"]
    assert types("q=NORTH") == ["line_crossed"]  # line id, case-insensitive
    assert types("q=unreachable") == ["camera_error"]  # metadata text
    assert types("q=Yard") == ["line_crossed"]  # camera name (cam-b = Yard)
    item = client.get("/api/v1/events?category=line", headers=viewer).json()["items"][0]
    assert item["camera_name"] == "Yard" and item["device_name"] == "Dock"
    assert client.get("/api/v1/events?category=bogus", headers=viewer).status_code == 400
    # CSV: operators/admins only (events:export)
    assert client.get("/api/v1/events/export.csv", headers=viewer).status_code == 403
    r = client.get("/api/v1/events/export.csv?category=motion&category=zone", headers=admin)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert sorted(row["event_type"] for row in rows) == ["motion_started", "object_entered_zone"]
    assert rows[0]["device"] == "Dock" and rows[0]["camera"] == "Gate"


# ---------------------------------------------------------------------------- observations


def test_observations_group_per_camera_track_never_across_cameras(client: TestClient, admin: dict, db: Session):
    agent = Agent(client)
    agent.register(enroll(client, admin))
    agent.heartbeat()
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    agent.upload([
        person("cam-a", "7", occurred_at=t0.isoformat()),
        person("cam-a", "7", "person_crossed_line", line_id="door",
               occurred_at=(t0 + timedelta(seconds=4)).isoformat()),
        person("cam-a", "7", "person_left", duration_s=9.5, heading="right", confidence=0.91,
               attributes={"upper_color": {"value": "blue", "confidence": 0.7, "source": "local"}},
               occurred_at=(t0 + timedelta(seconds=9)).isoformat()),
        # same temporary id on another camera: a different observation
        person("cam-b", "7", occurred_at=(t0 + timedelta(seconds=20)).isoformat()),
    ])  # fmt: skip
    body = client.get("/api/v1/observations", headers=admin).json()
    assert body["total"] == 2
    a = next(o for o in body["items"] if o["camera_id"] == "cam-a")
    assert a["track_id"] == "7" and a["camera_name"] == "Gate" and a["duration_s"] == 9.5
    assert a["direction"] == "right" and a["confidence"] == 0.91 and a["ended"] is True
    assert a["lines_crossed"] == ["door"] and a["attributes"]["upper_color"]["value"] == "blue"
    assert a["detector"] == "yolox" and a["event_count"] == 3
    client.patch("/api/v1/system/settings", json={"show_person_attributes": False}, headers=admin)
    hidden = next(
        o for o in client.get("/api/v1/observations", headers=admin).json()["items"] if o["camera_id"] == "cam-a"
    )
    assert hidden["attributes"] is None and hidden["attributes_hidden"] is True


# ---------------------------------------------------------------------------- overview + device events


def test_overview_counters(client: TestClient, admin: dict, viewer: dict, db: Session, settings):
    settings.rate_heartbeat_burst = 50
    a, b = Agent(client, "a"), Agent(client, "b")
    token = enroll(client, admin, max_uses=2)
    a.register(token)
    b.register(token)
    a.heartbeat(ai={"enabled": True, "mode": "active"})
    b.heartbeat(ai={"enabled": False, "mode": "disabled"},
                camera_list=[{"id": "cam-c", "name": "Hall", "source_type": "rtsp", "status": "error"}],
                cameras={"total": 1, "by_status": {"error": 1}})  # fmt: skip
    a.upload([event(), event(), event("cam-a", "motion_ended"), person("cam-a", "1"),
              person("cam-a", "1", "person_left"), person("cam-b", "2")])  # fmt: skip
    o = client.get("/api/v1/dashboard/overview", headers=admin).json()
    assert o["cameras"] == {"total": 3, "online": 2, "offline": 1, "by_status": {"online": 2, "error": 1}}
    assert o["devices"]["total"] == 2 and o["devices"]["active"] == 2
    assert o["devices"]["ai_active"] == 1 and o["devices"]["without_ai"] == 1
    assert o["events_today"]["motion"] == 2  # motion_started only — never mixed with person events
    assert o["events_today"]["person_events"] == 3
    assert o["observations_today"] == 2  # (cam-a, 1) and (cam-b, 2)
    assert len(o["recent_events"]) == 6
    v = client.get("/api/v1/dashboard/overview", headers=viewer).json()
    assert v["observations_today"] is None and v["cameras"]["total"] == 3  # viewer: no observations


def test_device_health_transitions_become_events(client: TestClient, admin: dict, db: Session, settings):
    agent = Agent(client, "Edge-7")
    agent.register(enroll(client, admin))
    agent.heartbeat()
    device = db.get(EdgeDevice, uuid.UUID(agent.device_id))
    device.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    db.commit()
    mark_stale_devices(db, settings)
    db.commit()
    agent.heartbeat()
    types = [e["event_type"] for e in client.get("/api/v1/events?category=device_error", headers=admin).json()["items"]]
    assert types == ["device_online", "device_offline"]
    assert db.scalar(select(func.count()).select_from(CameraEvent).where(CameraEvent.source == "system")) == 2


def test_csv_file_name_visible_to_the_browser(client: TestClient, admin: dict):
    """The dashboard runs on another origin: Content-Disposition must be exposed by CORS."""
    r = client.get("/api/v1/events/export.csv", headers=admin | {"Origin": "http://localhost:3000"})
    assert r.status_code == 200
    assert "content-disposition" in r.headers["access-control-expose-headers"].lower()
