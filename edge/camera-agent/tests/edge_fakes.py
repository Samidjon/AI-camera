"""In-process fake of the central server's device API (the MVP 06 contract), as an httpx transport.

Switches simulate the failure modes from the MVP 05 spec: server down, internet off (connect
errors), TLS failure, lost acknowledgements, rejected events, revocation, configuration push.
"""

from __future__ import annotations

import json
import secrets
import threading

import httpx

ENROLLMENT_TOKEN = "enroll-test-token-123456"


class FakeCentralServer:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.devices: dict[str, dict] = {}  # remote id -> {"secret", "name", "revoked"}
        self.events: dict[str, dict] = {}  # event id -> event (idempotent store)
        self.received_batches = 0
        self.heartbeats: list[dict] = []
        self.config_version = 0
        self.config: dict = {}
        self.acks: list[dict] = []
        # failure switches
        self.down = False  # connection refused (server process stopped / internet off)
        self.tls_error = False  # certificate verification failure
        self.http_500 = False
        self.lose_ack = False  # store the batch, then fail the response (ack lost in transit)
        self.reject_ids: set[str] = set()
        self.registrations = 0

    # ------------------------------------------------------------------ helpers

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def revoke_all(self) -> None:
        with self.lock:
            for d in self.devices.values():
                d["revoked"] = True

    def push_config(self, config: dict) -> None:
        with self.lock:
            self.config_version += 1
            self.config = config

    @staticmethod
    def _json(status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    def _auth(self, request: httpx.Request) -> dict | None:
        device_id = request.headers.get("X-ACI-Device-ID", "")
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        d = self.devices.get(device_id)
        if d is None or d["revoked"] or not secrets.compare_digest(d["secret"], token):
            return None
        return d

    # ------------------------------------------------------------------ routing

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
        if self.tls_error:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request)
        if self.http_500:
            return self._json(500, {"error": {"code": "internal_error", "message": "boom"}})
        path = request.url.path
        with self.lock:
            if path == "/api/v1/devices/register" and request.method == "POST":
                return self._register(request)
            device = self._auth(request)
            if device is None:
                return self._json(401, {"error": {"code": "device_revoked", "message": "Device revoked"}})
            if path.endswith("/heartbeat"):
                self.heartbeats.append(json.loads(request.content))
                return self._json(200, {"config_version": self.config_version, "server_time": "now"})
            if path == "/api/v1/events/batch":
                return self._batch(request)
            if path.endswith("/configuration/ack"):
                self.acks.append(json.loads(request.content))
                return self._json(200, {"ok": True})
            if path.endswith("/configuration"):
                return self._json(200, {"version": self.config_version, "config": self.config})
        return self._json(404, {"error": {"code": "not_found", "message": "Not found"}})

    def _register(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {ENROLLMENT_TOKEN}":
            return self._json(
                401, {"error": {"code": "invalid_enrollment_token", "message": "Invalid enrollment token"}}
            )
        body = json.loads(request.content)
        remote_id = body["device_id"]
        secret = secrets.token_urlsafe(32)
        self.devices[remote_id] = {"secret": secret, "name": body["device_name"], "revoked": False}
        self.registrations += 1
        return self._json(201, {"device_id": remote_id, "device_secret": secret, "heartbeat_interval_s": 30})

    def _batch(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.received_batches += 1
        accepted, duplicate, rejected = [], [], []
        for e in body["events"]:
            if e["id"] in self.reject_ids:
                rejected.append({"id": e["id"], "reason": "invalid event_type"})
            elif e["id"] in self.events:
                duplicate.append(e["id"])
            else:
                self.events[e["id"]] = e
                accepted.append(e["id"])
        if self.lose_ack:
            return self._json(502, {"error": {"code": "bad_gateway", "message": "proxy lost the response"}})
        return self._json(200, {"accepted": accepted, "duplicate": duplicate, "rejected": rejected})
