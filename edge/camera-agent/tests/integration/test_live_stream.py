"""Multiplexed live stream over one WebSocket (MVP 07 monitoring wall)."""

from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import FakeFactory, auth, make_token, wait_for


def _add(client: TestClient, factory: FakeFactory, n: int) -> list[str]:
    ids = []
    for i in range(n):
        factory.add(f"live-{i}.mp4", width=320, height=240)
        body = {"name": f"Live {i}", "source_type": "file", "source_uri": f"live-{i}.mp4", "connect": True}
        r = client.post("/api/v1/cameras", json=body, headers=auth())
        ids.append(r.json()["id"])
    assert wait_for(lambda: client.get("/api/v1/system/status").json()["cameras_by_status"]["online"] == n, 8)
    return ids


def _token(perms=None) -> str:
    return auth(perms)["Authorization"].removeprefix("Bearer ")


def test_live_requires_auth_and_permission(client: TestClient):
    with client.websocket_connect("/api/v1/live") as ws:
        ws.send_json({"type": "auth", "token": "garbage"})
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect("/api/v1/live") as ws:
        ws.send_json({"type": "auth", "token": make_token(["events:read"])})
        assert ws.receive_json()["type"] == "error"


def test_one_socket_carries_several_cameras(client: TestClient, factory: FakeFactory):
    ids = _add(client, factory, 3)
    with client.websocket_connect("/api/v1/live") as ws:
        ws.send_json({"type": "auth", "token": _token(["cameras:read"])})
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"type": "subscribe", "cameras": ids, "max_fps": 15, "max_width": 160, "layers": "all"})
        frames: dict[str, int] = {}
        statuses = {}
        for _ in range(80):
            msg = ws.receive()
            if msg.get("bytes"):
                data = msg["bytes"]
                (hlen,) = struct.unpack(">I", data[:4])
                header = json.loads(data[4 : 4 + hlen])
                jpeg = data[4 + hlen :]
                assert jpeg[:2] == b"\xff\xd8"  # JPEG
                frames[header["camera_id"]] = frames.get(header["camera_id"], 0) + 1
            elif msg.get("text"):
                body = json.loads(msg["text"])
                if body["type"] == "status":
                    statuses = body["cameras"]
            if len(frames) == 3 and all(v >= 2 for v in frames.values()) and statuses:
                break
        assert set(frames) == set(ids) and all(v >= 2 for v in frames.values())
        assert all(s["status"] == "online" for s in statuses.values())
        # resubscribe to a single camera: only that one keeps streaming
        ws.send_json({"type": "subscribe", "cameras": [ids[0]], "max_fps": 15, "max_width": 160})
        later = set()
        for _ in range(40):
            msg = ws.receive()
            if msg.get("bytes"):
                (hlen,) = struct.unpack(">I", msg["bytes"][:4])
                later.add(json.loads(msg["bytes"][4 : 4 + hlen])["camera_id"])
        assert ids[0] in later
