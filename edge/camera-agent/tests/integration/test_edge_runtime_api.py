"""MVP 05 — the whole agent (FastAPI app) with a fake central server: device API, multiple cameras,
server outage and AI failure while syncing."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aci_agent.api.app import create_app
from aci_agent.camera.manager import CameraManager
from aci_agent.configuration.settings import AgentSettings, ServerSettings
from aci_agent.events.models import Event
from tests.conftest import FakeFactory, auth, make_token, wait_for
from tests.edge_fakes import ENROLLMENT_TOKEN, FakeCentralServer

VIEWER = {"Authorization": f"Bearer {make_token(['cameras:read', 'devices:read'], role='viewer')}"}


@pytest.fixture
def central() -> FakeCentralServer:
    return FakeCentralServer()


@pytest.fixture
def linked_client(settings: AgentSettings, manager: CameraManager, central: FakeCentralServer) -> Iterator[TestClient]:
    settings = settings.model_copy(
        update={
            "server": ServerSettings(
                url="https://central.example.test", enrollment_token=ENROLLMENT_TOKEN,
                heartbeat_interval_s=5, sync_interval_s=0.5, backoff_initial_s=0.1, backoff_max_s=1,
            )
        }
    )  # fmt: skip
    app = create_app(settings, manager=manager, server_transport=central.transport())
    with TestClient(app) as c:
        yield c


def _add_cameras(client: TestClient, factory: FakeFactory, n: int) -> None:
    for i in range(n):
        factory.add(f"file-{i}.mp4")
        r = client.post(
            "/api/v1/cameras",
            json={"name": f"Cam {i}", "source_type": "file", "source_uri": f"file-{i}.mp4", "connect": True},
            headers=auth(),
        )
        assert r.status_code == 201, r.text


def test_device_api_reports_identity_hardware_and_standalone_sync(client: TestClient):
    d = client.get("/api/v1/device", headers=VIEWER).json()
    assert len(d["device_id"]) == 36 and d["device_name"]
    assert d["hardware"]["architecture"] and d["metrics"]["cpu"]["percent"] is not None
    assert d["sync"]["state"] == "unconfigured" and d["sync"]["link"] is None
    status = client.get("/api/v1/system/status").json()
    assert status["device"]["device_id"] == d["device_id"] and status["sync"]["state"] == "unconfigured"


def test_device_management_requires_permission(client: TestClient):
    assert client.get("/api/v1/device").status_code == 401
    assert client.patch("/api/v1/device", json={"name": "x"}, headers=VIEWER).status_code == 403
    body = {"server_url": "https://central.example.test", "enrollment_token": ENROLLMENT_TOKEN}
    assert client.post("/api/v1/device/link", json=body, headers=VIEWER).status_code == 403
    assert client.post("/api/v1/device/backups", headers=VIEWER).status_code == 403


def test_rename_backup_and_logs(client: TestClient):
    assert client.patch("/api/v1/device", json={"name": "Warehouse gate"}, headers=auth()).json()["device_name"] == (
        "Warehouse gate"
    )
    b = client.post("/api/v1/device/backups", headers=auth())
    assert b.status_code == 201 and set(b.json()["files"]) == {"agent", "events"}
    assert client.get("/api/v1/device/backups", headers=auth()).json()[0]["id"] == b.json()["id"]
    assert client.get("/api/v1/device/logs?level=bogus", headers=auth()).status_code == 422
    assert isinstance(client.get("/api/v1/device/logs", headers=auth()).json(), list)


def test_link_requires_https(client: TestClient):
    r = client.post(
        "/api/v1/device/link",
        json={"server_url": "http://central.example.test", "enrollment_token": ENROLLMENT_TOKEN},
        headers=auth(),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "insecure_server_url"


def test_linked_agent_with_cameras_survives_outage_and_ai_failure(
    linked_client: TestClient, factory: FakeFactory, central: FakeCentralServer
):
    c = linked_client
    assert wait_for(lambda: bool(central.heartbeats), 5)
    assert central.registrations == 1
    assert c.get("/api/v1/device/sync", headers=auth()).json()["state"] == "online"
    _add_cameras(c, factory, 3)
    assert wait_for(lambda: c.get("/api/v1/system/status").json()["cameras_by_status"].get("online") == 3, 8)

    events = c.app.state.events
    sent = []
    for i in range(10):
        e = Event(camera_id=f"cam-{i % 3}", event_type="motion_started")
        events.append(e)
        sent.append(e.id)
    assert wait_for(lambda: set(sent) <= set(central.events), 8)

    # Server outage: cameras keep streaming, events are queued.
    # (With an empty queue the outage is noticed at the next heartbeat; new events reveal it at once.)
    central.down = True
    offline = []
    for _ in range(5):
        e = Event(camera_id="cam-1", event_type="motion_ended")
        events.append(e)
        offline.append(e.id)
    assert wait_for(lambda: c.get("/api/v1/device/sync", headers=auth()).json()["state"] == "offline", 8)
    assert wait_for(lambda: c.get("/api/v1/device/sync", headers=auth()).json()["queue"]["pending"] == 5, 5)
    assert c.get("/api/v1/system/status").json()["cameras_by_status"].get("online") == 3

    # AI failure (model file missing in the test environment) while offline: nothing else is affected.
    c.post("/api/v1/ai/enable", headers=auth())
    assert wait_for(lambda: c.get("/api/v1/ai/status", headers=auth()).json()["mode"] in ("offline", "active"), 8)

    central.down = False
    assert wait_for(lambda: set(offline) <= set(central.events), 10)
    s = c.get("/api/v1/device/sync", headers=auth()).json()
    assert s["state"] == "online" and s["queue"]["pending"] == 0
    assert c.get("/api/v1/system/status").json()["cameras_by_status"].get("online") == 3
    assert len(central.events) == 15  # each event exactly once


def test_revocation_through_api_keeps_cameras_running(
    linked_client: TestClient, factory: FakeFactory, central: FakeCentralServer
):
    c = linked_client
    assert wait_for(lambda: c.get("/api/v1/device/sync", headers=auth()).json()["state"] == "online", 5)
    _add_cameras(c, factory, 2)
    central.revoke_all()
    c.post("/api/v1/device/sync", headers=auth())
    assert wait_for(lambda: c.get("/api/v1/device/sync", headers=auth()).json()["state"] == "revoked", 8)
    assert wait_for(lambda: c.get("/api/v1/system/status").json()["cameras_by_status"].get("online") == 2, 8)
    d = c.get("/api/v1/device", headers=auth()).json()
    assert d["sync"]["link"]["revoked"] is True
    assert "secret" not in str(d).lower().replace("device_secret", "")


def test_central_configuration_applies_camera_motion_zones_and_events(
    linked_client: TestClient, factory: FakeFactory, central: FakeCentralServer
):
    c = linked_client
    assert wait_for(lambda: bool(central.heartbeats), 5)
    _add_cameras(c, factory, 2)
    cams = c.get("/api/v1/cameras", headers=auth()).json()
    a, b = cams[0]["id"], cams[1]["id"]
    central.push_config({
        "ai_enabled": False,
        "events": {"retention_days": 12},
        "cameras": {
            a: {"max_fps": 5, "ai_enabled": False, "motion": {"sensitivity": 90, "algorithm": "knn"},
                "zones": [{"name": "Door", "role": "monitor", "polygon": [[0, 0], [0.5, 0], [0.5, 0.5]]}]},
            b: {"is_enabled": False},
            "missing-camera": {"max_fps": 2},
        },
    })  # fmt: skip
    c.app.state.network._next_heartbeat = 0
    c.post("/api/v1/device/sync", headers=auth())
    assert wait_for(lambda: bool(central.acks), 8)
    ack = central.acks[-1]
    assert ack["status"] == "partial" and "missing-camera" in ack["errors"]["cameras"]
    assert set(ack["applied"]) >= {"ai_enabled", "events"}
    cam_a = c.get(f"/api/v1/cameras/{a}", headers=auth()).json()
    assert cam_a["options"]["max_fps"] == 5 and cam_a["options"]["ai_enabled"] is False
    vision = c.get(f"/api/v1/cameras/{a}/vision", headers=auth()).json()["config"]
    assert vision["sensitivity"] == 90 and vision["algorithm"] == "knn"
    assert [z["name"] for z in c.get(f"/api/v1/cameras/{a}/zones", headers=auth()).json()] == ["Door"]
    assert c.get(f"/api/v1/cameras/{b}", headers=auth()).json()["is_enabled"] is False
    assert c.app.state.events.settings.retention_days == 12
    assert c.get("/api/v1/ai/status", headers=auth()).json()["enabled"] is False
    hb = central.heartbeats[-1]
    assert {cam["id"] for cam in hb["camera_list"]} == {a, b}
    assert "source_uri" not in str(hb["camera_list"])  # no stream URLs / credentials leave the device
