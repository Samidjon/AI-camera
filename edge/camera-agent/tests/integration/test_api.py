from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from tests.conftest import FakeFactory, auth, make_token, wait_for

URL = "rtsp://10.0.0.21:554/stream1"
VIEWER = ["cameras:read", "events:read", "devices:read"]
OPERATOR = ["cameras:read", "cameras:control", "events:read", "events:ack", "devices:read"]


def _create(client: TestClient, **overrides):
    body = {"name": "Entrance", "source_type": "rtsp", "source_uri": URL, "location": "Lobby"} | overrides
    return client.post("/api/v1/cameras", json=body, headers=auth())


def test_health_and_status_are_public(client: TestClient):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    s = client.get("/api/v1/system/status").json()
    assert s["database_ok"] is True and s["auth_configured"] is True and s["cameras_total"] == 0


def test_management_requires_token(client: TestClient):
    assert client.get("/api/v1/cameras").status_code == 401
    bad = client.get("/api/v1/cameras", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthorized"


def test_role_permissions_are_enforced(client: TestClient, factory: FakeFactory):
    factory.add(URL)
    viewer = {"Authorization": f"Bearer {make_token(VIEWER, 'viewer')}"}
    operator = {"Authorization": f"Bearer {make_token(OPERATOR)}"}
    body = {"name": "X", "source_type": "rtsp", "source_uri": URL}
    assert client.post("/api/v1/cameras", json=body, headers=viewer).status_code == 403
    assert client.post("/api/v1/cameras", json=body, headers=operator).status_code == 403  # manage ≠ control
    cam = _create(client).json()
    assert client.get("/api/v1/cameras", headers=viewer).status_code == 200
    assert client.post(f"/api/v1/cameras/{cam['id']}/disconnect", headers=viewer).status_code == 403
    assert client.post(f"/api/v1/cameras/{cam['id']}/disconnect", headers=operator).status_code == 200
    assert client.delete(f"/api/v1/cameras/{cam['id']}", headers=operator).status_code == 403


def test_token_without_permissions_claim_is_rejected(client: TestClient):
    import jwt

    from tests.conftest import JWT_SECRET

    legacy = jwt.encode({"sub": "u", "type": "access", "role": "administrator"}, JWT_SECRET, algorithm="HS256")
    r = client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 401


def test_crud_flow_and_password_never_exposed(client: TestClient, factory: FakeFactory, caplog):
    factory.add(URL, width=640, height=360)
    caplog.set_level(logging.DEBUG)
    r = _create(client, username="admin", password="S3cr3t-Pa55")
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["has_credentials"] is True
    assert "S3cr3t" not in r.text and "password" not in cam
    # FPS is measured from real frames, so it appears only after the second frame.
    assert wait_for(lambda: (client.get(f"/api/v1/cameras/{cam['id']}", headers=auth()).json()["fps"] or 0) > 0)
    got = client.get(f"/api/v1/cameras/{cam['id']}", headers=auth()).json()
    assert got["status"] == "online" and got["resolution"] == {"width": 640, "height": 360}

    r = client.patch(f"/api/v1/cameras/{cam['id']}", json={"name": "Main entrance"}, headers=auth())
    assert r.json()["name"] == "Main entrance" and r.json()["has_credentials"] is True

    r = client.patch(f"/api/v1/cameras/{cam['id']}", json={"clear_credentials": True}, headers=auth())
    assert r.json()["has_credentials"] is False

    assert client.delete(f"/api/v1/cameras/{cam['id']}", headers=auth()).status_code == 204
    assert client.get(f"/api/v1/cameras/{cam['id']}", headers=auth()).status_code == 404
    assert "S3cr3t-Pa55" not in caplog.text


def test_credentials_typed_into_url_are_moved_to_secret_store(client: TestClient, factory: FakeFactory):
    factory.add(URL)
    r = _create(client, source_uri="rtsp://admin:hunter2@10.0.0.21:554/stream1")
    cam = r.json()
    assert cam["source"] == URL and cam["has_credentials"] is True
    assert "hunter2" not in r.text
    listing = client.get("/api/v1/cameras", headers=auth()).text
    assert "hunter2" not in listing


def test_validation_errors_do_not_echo_input(client: TestClient):
    r = _create(client, source_uri="ftp://x", password="leak-me")
    assert r.status_code == 422
    assert "leak-me" not in r.text
    r = client.post("/api/v1/cameras", json={"name": "", "source_type": "vhs", "source_uri": "x"}, headers=auth())
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_test_connection_endpoints(client: TestClient, factory: FakeFactory):
    factory.add(URL, width=1280, height=720)
    r = client.post("/api/v1/cameras/test", json={"source_type": "rtsp", "source_uri": URL}, headers=auth())
    body = r.json()
    assert body["ok"] is True and body["resolution"] == {"width": 1280, "height": 720}
    r = client.post(
        "/api/v1/cameras/test", json={"source_type": "rtsp", "source_uri": "rtsp://10.9.9.9/none"}, headers=auth()
    )
    assert r.json()["ok"] is False and r.json()["failure_kind"] == "unreachable"
    assert sum(s.open_devices for s in factory.sources.values()) == 0  # test released everything


def test_mjpeg_preview_and_snapshot(client: TestClient, factory: FakeFactory):
    factory.add(URL, fps=30)
    cam = _create(client).json()
    assert wait_for(lambda: client.get(f"/api/v1/cameras/{cam['id']}", headers=auth()).json()["status"] == "online")
    tok = client.post(f"/api/v1/cameras/{cam['id']}/stream-token", headers=auth(VIEWER)).json()
    assert tok["expires_in"] > 0

    snap = client.get(tok["snapshot_url"])
    assert snap.status_code == 200 and snap.headers["content-type"] == "image/jpeg"
    assert snap.content[:2] == b"\xff\xd8"  # JPEG magic

    with client.stream("GET", tok["stream_url"]) as resp:
        assert resp.headers["content-type"].startswith("multipart/x-mixed-replace")
        received = b""
        for chunk in resp.iter_bytes():
            received += chunk
            if received.count(b"\xff\xd8") >= 2:
                break
    assert b"--acimjpegframe" in received


def test_stream_token_is_scoped_to_camera(client: TestClient, factory: FakeFactory):
    factory.add(URL)
    factory.add("rtsp://10.0.0.22/s")
    a = _create(client).json()
    b = _create(client, name="B", source_uri="rtsp://10.0.0.22/s").json()
    tok = client.post(f"/api/v1/cameras/{a['id']}/stream-token", headers=auth()).json()["token"]
    r = client.get(f"/api/v1/cameras/{b['id']}/snapshot.jpg", params={"token": tok})
    assert r.status_code == 403
    access = make_token(["*"])  # an access token is not a stream token
    assert client.get(f"/api/v1/cameras/{a['id']}/snapshot.jpg", params={"token": access}).status_code == 403


def test_preview_of_disconnected_camera_is_conflict(client: TestClient, factory: FakeFactory):
    factory.add(URL)
    cam = _create(client, connect=False).json()
    assert cam["status"] == "offline"
    tok = client.post(f"/api/v1/cameras/{cam['id']}/stream-token", headers=auth()).json()["token"]
    assert client.get(f"/api/v1/cameras/{cam['id']}/snapshot.jpg", params={"token": tok}).status_code == 409


def test_camera_groups(client: TestClient, factory: FakeFactory):
    factory.add(URL)
    g = client.post("/api/v1/camera-groups", json={"name": "Perimeter"}, headers=auth()).json()
    assert client.post("/api/v1/camera-groups", json={"name": "perimeter"}, headers=auth()).status_code == 409
    cam = _create(client, group_id=g["id"]).json()
    assert cam["group_id"] == g["id"]
    groups = client.get("/api/v1/camera-groups", headers=auth()).json()
    assert groups[0]["camera_count"] == 1
    assert client.delete(f"/api/v1/camera-groups/{g['id']}", headers=auth()).status_code == 204
    assert client.get(f"/api/v1/cameras/{cam['id']}", headers=auth()).json()["group_id"] is None
    r = _create(client, name="Y", group_id="missing")
    assert r.status_code == 422


def test_camera_limit(client: TestClient, settings, factory: FakeFactory):
    settings.max_cameras = 2
    for i in range(2):
        factory.add(f"rtsp://10.0.2.{i}/s")
        assert _create(client, name=f"C{i}", source_uri=f"rtsp://10.0.2.{i}/s", connect=False).status_code == 201
    r = _create(client, name="C3", source_uri="rtsp://10.0.2.9/s", connect=False)
    assert r.status_code == 409 and r.json()["error"]["code"] == "camera_limit"


def test_cors_origin_regex(settings, manager):
    from aci_agent.api.app import create_app

    s = settings.model_copy(update={"cors_origin_regex": r"https://camera-agent(-[a-z0-9-]+)?\.vercel\.app"})
    with TestClient(create_app(s, manager=manager)) as c:
        ok = c.get("/api/v1/health", headers={"Origin": "https://camera-agent.vercel.app"})
        assert ok.headers.get("access-control-allow-origin") == "https://camera-agent.vercel.app"
        assert "access-control-allow-origin" not in c.get("/api/v1/health", headers={"Origin": "https://x.example"}).headers
