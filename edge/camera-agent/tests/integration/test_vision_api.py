from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import FakeFactory, auth, make_token, wait_for
from tests.vision_scenes import moving_box_scene

URL = "rtsp://10.0.6.1/live"
VIEWER = {"Authorization": f"Bearer {make_token(['cameras:read', 'events:read', 'devices:read'], 'viewer')}"}


def _camera(client: TestClient, factory: FakeFactory) -> str:
    src = factory.add(URL, width=640, height=360, fps=25)
    src.scene = moving_box_scene(speed=8, start_frame=20)
    r = client.post("/api/v1/cameras", json={"name": "Yard", "source_type": "rtsp", "source_uri": URL}, headers=auth())
    return r.json()["id"]


def test_vision_config_roundtrip_and_validation(client: TestClient, factory: FakeFactory):
    cid = _camera(client, factory)
    got = client.get(f"/api/v1/cameras/{cid}/vision", headers=auth()).json()
    assert got["config"]["algorithm"] == "mog2"
    body = got["config"] | {"algorithm": "knn", "sensitivity": 80, "process_fps": 5}
    r = client.put(f"/api/v1/cameras/{cid}/vision/config", json=body, headers=auth())
    assert r.status_code == 200 and r.json()["algorithm"] == "knn"
    bad = client.put(f"/api/v1/cameras/{cid}/vision/config", json=body | {"sensitivity": 500}, headers=auth())
    assert bad.status_code == 422
    unknown = client.put(f"/api/v1/cameras/{cid}/vision/config", json=body | {"face_recognition": True}, headers=auth())
    assert unknown.status_code == 422  # extra fields rejected
    assert client.put(f"/api/v1/cameras/{cid}/vision/config", json=body, headers=VIEWER).status_code == 403


def test_zone_and_line_crud(client: TestClient, factory: FakeFactory):
    cid = _camera(client, factory)
    poly = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.6], [0.1, 0.6]]
    z = client.post(f"/api/v1/cameras/{cid}/zones", json={"name": "Entry", "polygon": poly}, headers=auth())
    assert z.status_code == 201 and z.json()["role"] == "monitor"
    zid = z.json()["id"]
    assert (
        client.post(f"/api/v1/cameras/{cid}/zones", json={"name": "Entry", "polygon": poly}, headers=auth()).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/cameras/{cid}/zones", json={"name": "Bad", "polygon": [[0, 0], [2, 0], [1, 1]]}, headers=auth()
        ).status_code
        == 422
    )
    r = client.put(f"/api/v1/cameras/{cid}/zones/{zid}", json={"name": "Trees", "role": "exclude", "polygon": poly},
                   headers=auth())  # fmt: skip
    assert r.json()["role"] == "exclude"
    assert (
        client.post(f"/api/v1/cameras/{cid}/zones", json={"name": "V", "polygon": poly}, headers=VIEWER).status_code
        == 403
    )

    ln = client.post(
        f"/api/v1/cameras/{cid}/lines", json={"name": "Door", "a": [0.5, 0.1], "b": [0.5, 0.9]}, headers=auth()
    )
    assert ln.status_code == 201
    lines = client.get(f"/api/v1/cameras/{cid}/lines", headers=VIEWER).json()
    assert lines[0]["counts_today"] == {"forward": 0, "backward": 0}
    short = client.post(
        f"/api/v1/cameras/{cid}/lines", json={"name": "S", "a": [0.5, 0.5], "b": [0.5, 0.505]}, headers=auth()
    )
    assert short.status_code == 422

    assert client.delete(f"/api/v1/cameras/{cid}/zones/{zid}", headers=auth()).status_code == 204
    assert client.delete(f"/api/v1/cameras/{cid}/lines/{ln.json()['id']}", headers=auth()).status_code == 204
    assert client.get(f"/api/v1/cameras/{cid}/zones", headers=auth()).json() == []


def test_events_api_filters_and_pagination(client: TestClient, factory: FakeFactory):
    cid = _camera(client, factory)
    client.post(
        f"/api/v1/cameras/{cid}/lines", json={"name": "Door", "a": [0.5, 0.05], "b": [0.5, 0.95]}, headers=auth()
    )

    def crossed():
        r = client.get("/api/v1/events", params={"event_type": "line_crossed", "camera_id": cid}, headers=VIEWER)
        return r.json()["items"]

    assert wait_for(crossed, timeout=15)
    items = crossed()
    assert items[0]["metadata"]["direction"] == "forward" and items[0]["source"] == "standard"
    lines = client.get(f"/api/v1/cameras/{cid}/lines", headers=auth()).json()
    assert lines[0]["counts_today"]["forward"] >= 1

    page = client.get("/api/v1/events", params={"camera_id": cid, "limit": 1}, headers=VIEWER).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    nxt = client.get("/api/v1/events", params={"camera_id": cid, "limit": 1, "cursor": page["next_cursor"]},
                     headers=VIEWER).json()  # fmt: skip
    assert nxt["items"][0]["id"] != page["items"][0]["id"]
    summary = client.get("/api/v1/events/summary", headers=VIEWER).json()
    assert summary["counts"].get("line_crossed", 0) >= 1
    no_perm = {"Authorization": f"Bearer {make_token(['cameras:read'], 'custom')}"}
    assert client.get("/api/v1/events", headers=no_perm).status_code == 403


def test_live_state_and_overlay_preview(client: TestClient, factory: FakeFactory):
    cid = _camera(client, factory)
    client.post(f"/api/v1/cameras/{cid}/zones", json={"name": "Z", "polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]},
                headers=auth())  # fmt: skip
    assert wait_for(
        lambda: (
            client.get(f"/api/v1/cameras/{cid}/vision", headers=auth()).json()["state"].get("frames_analysed", 0) > 5
        ),
        timeout=10,
    )
    state = client.get(f"/api/v1/cameras/{cid}/vision", headers=auth()).json()["state"]
    assert state["status"] in ("active", "warming_up") and state["processing_ms"] is not None
    tok = client.post(f"/api/v1/cameras/{cid}/stream-token", headers=auth()).json()
    plain = client.get(tok["snapshot_url"]).content
    overlay = client.get(tok["snapshot_url"] + "&layers=all").content
    assert plain[:2] == overlay[:2] == b"\xff\xd8" and plain != overlay
    status = client.get("/api/v1/system/status").json()
    assert status["vision"]["workers"] == 1 and "events_total" in status["events"]
    assert status["resources"]["process_rss_mb"] > 0
