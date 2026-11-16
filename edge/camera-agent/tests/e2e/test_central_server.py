"""End-to-end: real edge agents ↔ the real central server over HTTP (MVP 06 Definition of Done).

Starts ``apps/api-server`` with uvicorn on a temporary SQLite database (its own uv environment),
then drives several agents (network manager + local event store, as in production) against it:
registration, concurrent uploads, duplicate uploads, server outage and recovery, configuration
versions (conflict, apply, acknowledgement), revocation of one device, offline detection.

Skipped when the server project or ``uv`` isn't available. Run: ``uv run pytest -m e2e``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from aci_agent.agent.device import DeviceIdentity
from aci_agent.configuration.settings import ServerSettings
from aci_agent.events.models import Event
from aci_agent.events.store import EventStore, EventStoreSettings
from aci_agent.networking.link import ServerLinkRepository
from aci_agent.security.crypto import SecretBox
from aci_agent.storage.database import Database
from aci_agent.synchronization.appliers import _apply_events
from aci_agent.synchronization.manager import LinkState, NetworkManager

pytestmark = pytest.mark.e2e

SERVER_DIR = Path(__file__).resolve().parents[4] / "apps" / "api-server"
UV = shutil.which("uv")
ADMIN_EMAIL = "e2e-admin@example.com"
ADMIN_PASSWORD = "e2e-password-0123456789"

if not (SERVER_DIR / "app").is_dir() or UV is None:
    pytest.skip("central server project or uv not available", allow_module_level=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CentralServer:
    def __init__(self, tmp: Path) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.env = {
            **os.environ,
            "ACI_DATABASE_URL": f"sqlite:///{(tmp / 'central.db').as_posix()}",
            "ACI_ENVIRONMENT": "development",
            "ACI_JWT_SECRET": "e2e-jwt-secret-0123456789abcdef0123456789",
            "ACI_LOG_JSON": "false",
            "ACI_LOG_LEVEL": "WARNING",
            "ACI_DEVICE_MONITOR_INTERVAL_S": "0.5",
            "ACI_DEVICE_OFFLINE_MIN_S": "15",
            "ACI_RATE_HEARTBEAT_BURST": "1000",
            "ACI_RATE_EVENTS_BURST": "1000",
            "ACI_RATE_EVENTS_PER_S": "200",
            "ACI_NEW_USER_PASSWORD": ADMIN_PASSWORD,
            "PYTHONIOENCODING": "utf-8",
        }
        self.env.pop("VIRTUAL_ENV", None)
        self.log = open(tmp / "server.log", "w", encoding="utf-8")  # noqa: SIM115
        self.proc: subprocess.Popen | None = None
        self._run("alembic", "upgrade", "head")
        self._run("python", "-m", "app.cli", "create-user", "--email", ADMIN_EMAIL, "--role", "administrator")

    def _run(self, *args: str) -> None:
        subprocess.run([UV, "run", *args], cwd=SERVER_DIR, env=self.env, check=True, capture_output=True, timeout=180)

    def start(self) -> None:
        # Run the server's own interpreter directly (not through `uv run`): stopping it must stop the
        # server itself, not just a wrapper process (Windows does not kill child processes).
        venv = SERVER_DIR / ".venv"
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        self.proc = subprocess.Popen(
            [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=SERVER_DIR, env=self.env, stdout=self.log, stderr=subprocess.STDOUT,
        )  # fmt: skip
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.url}/api/v1/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.3)
        raise RuntimeError("central server did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:  # make sure the port is really closed
            try:
                httpx.get(f"{self.url}/api/v1/health", timeout=0.5)
                time.sleep(0.2)
            except httpx.HTTPError:
                return


class EdgeAgent:
    def __init__(self, data: Path, name: str) -> None:
        self.db = Database(data / "agent.db")
        self.store = EventStore(
            data / "events.db",
            EventStoreSettings(flush_interval_s=0.02, initial_maintenance_delay_s=1e9, maintenance_interval_s=1e9),
        )
        self.identity = DeviceIdentity(self.db, name)
        self.links = ServerLinkRepository(self.db, SecretBox.from_settings("", data / "secret.key"))
        self.applied: dict = {}
        self.net = NetworkManager(
            ServerSettings(allow_insecure_http=True, backoff_initial_s=0.1, backoff_max_s=1, sync_batch_size=100,
                           heartbeat_interval_s=5,
                           request_timeout_s=5),
            self.identity, self.links, self.store,
            status_provider=lambda: {"cameras": {"total": 1, "by_status": {"online": 1}},
                                     "camera_list": [{"id": "cam-1", "name": "Door", "source_type": "rtsp",
                                                      "status": "online", "is_enabled": True}],
                                     "health": {"database_ok": True, "storage_paused": False}},
            config_appliers={"ai_enabled": lambda v: self.applied.__setitem__("ai_enabled", v),
                             "events": lambda v: _apply_events(self.store, v)},
        )  # fmt: skip

    def emit(self, n: int) -> list[str]:
        ids = []
        for i in range(n):
            e = Event(camera_id="cam-1", event_type="motion_started", metadata={"i": i})
            self.store.append(e)
            ids.append(e.id)
        assert self.store.flush(10)
        return ids

    def sync(self, steps: int = 200) -> None:
        for _ in range(steps):
            self.net.step()
            if self.store.sync_stats()["pending"] == 0 or self.net.state != LinkState.ONLINE:
                break
        self.net.step()  # as the background loop would: reports the cleared backlog right away

    def close(self) -> None:
        self.net.shutdown()
        self.store.close()
        self.db.close()


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    server = CentralServer(tmp_path_factory.mktemp("central"))
    server.start()
    yield server
    server.stop()
    server.log.close()


@pytest.fixture(scope="module")
def admin(central: CentralServer) -> httpx.Client:
    c = httpx.Client(base_url=central.url, timeout=10)
    token = c.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).json()
    c.headers["Authorization"] = f"Bearer {token['access_token']}"
    yield c
    c.close()


def _count_events(admin: httpx.Client, device_id: str | None = None) -> int:
    total, cursor = 0, None
    while True:
        params = {
            "limit": 500,
            **({"device_id": device_id} if device_id else {}),
            **({"cursor": cursor} if cursor else {}),
        }
        page = admin.get("/api/v1/events", params=params).json()
        total += len(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return total


def test_fleet_sync_end_to_end(central: CentralServer, admin: httpx.Client, tmp_path: Path):
    token = admin.post("/api/v1/devices/enrollment-tokens", json={"name": "e2e", "max_uses": 3}).json()["token"]
    agents = [EdgeAgent(tmp_path / f"edge{i}", f"edge-{i}") for i in range(3)]
    try:
        # --- several devices register
        for a in agents:
            assert a.net.register(central.url, token)["state"] == "online"
        devices = {d["id"]: d for d in admin.get("/api/v1/devices").json()}
        assert {a.identity.device_id for a in agents} <= set(devices)

        # --- concurrent uploads from all devices
        for a in agents:
            a.emit(250)
        threads = [threading.Thread(target=a.sync) for a in agents]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        for a in agents:
            assert a.store.sync_stats()["pending"] == 0, a.net.status()
            assert _count_events(admin, a.identity.device_id) == 250
        detail = admin.get(f"/api/v1/devices/{agents[0].identity.device_id}").json()
        assert detail["status"] == "online" and detail["cameras"][0]["external_id"] == "cam-1"

        # --- the same events again (acknowledgement lost): stored once
        rows = [dict(r) for r in agents[0].store.db.query("SELECT * FROM events LIMIT 50")]
        for r in rows:
            r["metadata"] = {}
        reply = agents[0].net._client.upload_events(rows)
        assert len(reply["duplicate"]) == 50 and not reply["accepted"]
        assert _count_events(admin, agents[0].identity.device_id) == 250

        # --- server unavailable: agents keep working and queue; recovery uploads everything once
        central.stop()
        offline_ids = agents[1].emit(40)
        agents[1].net.step()
        assert agents[1].net.state == LinkState.OFFLINE
        assert agents[1].store.sync_stats()["pending"] == 40
        assert agents[1].store.stats()["events_total"] == 290  # local storage continues
        central.start()
        agents[1].sync()
        assert agents[1].net.state == LinkState.ONLINE
        assert _count_events(admin, agents[1].identity.device_id) == 290
        got = admin.get(f"/api/v1/events/{offline_ids[0]}")
        assert got.status_code == 200

        # --- configuration: version conflict, apply + acknowledgement
        dev0 = agents[0].identity.device_id
        url = f"/api/v1/devices/{dev0}/configuration"
        v1 = admin.patch(url, json={"base_version": 0, "config": {"device_name": "Front gate", "ai_enabled": True,
                                                                  "events": {"retention_days": 9}}})  # fmt: skip
        assert v1.status_code == 200 and v1.json()["version"] == 1
        conflict = admin.patch(url, json={"base_version": 0, "config": {"ai_enabled": False}})
        assert conflict.status_code == 409
        agents[0].net._next_heartbeat = 0
        agents[0].net.step()
        assert agents[0].identity.device_name == "Front gate"
        assert agents[0].applied == {"ai_enabled": True}
        assert agents[0].store.settings.retention_days == 9
        state = admin.get(url).json()
        assert state["status"] == "applied" and state["applied_version"] == 1
        # unknown camera → partial, reported back to the server
        admin.patch(url, json={"base_version": 1, "config": {"cameras": {"cam-x": {"max_fps": 5}}}})
        agents[0].net._next_heartbeat = 0
        agents[0].net.step()
        assert admin.get(url).json()["status"] in ("applied", "partial")  # "cameras" has no applier here → ignored
        # rollback
        rb = admin.post(f"{url}/rollback", json={"to_version": 1, "base_version": 2})
        assert rb.status_code == 200 and rb.json()["version"] == 3

        # --- one device revoked: it stops syncing, keeps recording; the others are unaffected
        dev2 = agents[2].identity.device_id
        assert admin.post(f"/api/v1/devices/{dev2}/revoke").json()["status"] == "revoked"
        agents[2].emit(5)
        agents[2].net._next_heartbeat = 0
        agents[2].net.step()
        assert agents[2].net.state == LinkState.REVOKED
        assert agents[2].store.stats()["events_total"] == 255
        more = agents[0].emit(10)
        agents[0].sync()
        assert admin.get(f"/api/v1/events/{more[-1]}").status_code == 200

        # --- a device that stops sending heartbeats is marked offline by the server
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            agents[0].net._next_heartbeat = 0
            agents[0].net.step()  # agent 0 stays alive, agent 1 goes silent
            if admin.get(f"/api/v1/devices/{agents[1].identity.device_id}").json()["status"] == "offline":
                break
            time.sleep(1)
        assert admin.get(f"/api/v1/devices/{agents[1].identity.device_id}").json()["status"] == "offline"
        assert admin.get(f"/api/v1/devices/{dev0}").json()["status"] == "online"
        status = admin.get("/api/v1/system/status").json()
        assert status["devices"]["by_status"].get("revoked") == 1
    finally:
        for a in agents:
            a.close()
        if sys.exc_info()[0] is not None:
            central.log.flush()
