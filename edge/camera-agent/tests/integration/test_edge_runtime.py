"""MVP 05 — Edge Runtime: autonomy, offline sync, revocation, TLS policy, restart, power loss, recovery.

The central server is the in-process fake from ``tests/edge_fakes.py`` (MVP 06 contract). The
network manager is driven step by step (``step()``) so every scenario is deterministic.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aci_agent.agent.device import DeviceIdentity
from aci_agent.configuration.settings import ServerSettings
from aci_agent.events.models import Event
from aci_agent.events.store import EventStore, EventStoreSettings
from aci_agent.monitoring.system_log import SystemLogHandler, read_logs
from aci_agent.networking.client import InsecureServerURL
from aci_agent.networking.link import ServerLinkRepository
from aci_agent.security.crypto import SecretBox
from aci_agent.storage.backup import create_backup, list_backups, restore_backup
from aci_agent.storage.database import Database
from aci_agent.synchronization.backoff import Backoff
from aci_agent.synchronization.manager import LinkState, NetworkManager, RegistrationError
from tests.edge_fakes import ENROLLMENT_TOKEN, FakeCentralServer

SERVER = "https://central.example.test"


class Runtime:
    """Agent storage + network manager on a data directory; ``reopen()`` simulates a restart."""

    def __init__(self, data: Path, server: FakeCentralServer, **server_settings) -> None:
        self.data, self.server, self.server_settings = data, server, server_settings
        self._open()

    def _open(self) -> None:
        self.db = Database(self.data / "agent.db")
        self.box = SecretBox.from_settings("", self.data / "secret.key")
        self.store = EventStore(
            self.data / "events.db",
            EventStoreSettings(flush_interval_s=0.02, initial_maintenance_delay_s=1e9, maintenance_interval_s=1e9),
        )
        self.identity = DeviceIdentity(self.db, "edge-test")
        self.links = ServerLinkRepository(self.db, self.box)
        self.applied: dict = {}
        settings = ServerSettings(
            **({"backoff_initial_s": 0.1, "backoff_max_s": 1, "sync_batch_size": 50} | self.server_settings)
        )
        self.net = NetworkManager(
            settings, self.identity, self.links, self.store,
            status_provider=lambda: {"cameras": {"total": 2}}, metrics_provider=lambda: {"cpu": {"percent": 1}},
            config_appliers={"ai_enabled": lambda v: self.applied.__setitem__("ai_enabled", bool(v))},
            transport=self.server.transport(),
        )  # fmt: skip

    def close(self) -> None:
        self.net.shutdown()
        self.store.close()
        self.db.close()

    def reopen(self) -> None:
        self.close()
        self._open()

    def emit(self, n: int, camera: str = "cam-1", at: datetime | None = None) -> list[str]:
        ids = []
        for i in range(n):
            e = Event(camera_id=camera, event_type="motion_started", metadata={"i": i})
            if at is not None:
                e.occurred_at = at
            self.store.append(e)
            ids.append(e.id)
        assert self.store.flush(5)
        return ids

    def drain(self, max_steps: int = 50) -> None:
        for _ in range(max_steps):
            self.net.step()
            if self.store.sync_stats()["pending"] == 0 or self.net.state != LinkState.ONLINE:
                break


@pytest.fixture
def server() -> FakeCentralServer:
    return FakeCentralServer()


@pytest.fixture
def rt(tmp_path: Path, server: FakeCentralServer):
    r = Runtime(tmp_path / "data", server)
    yield r
    r.close()


def registered(rt: Runtime) -> Runtime:
    rt.net.register(SERVER, ENROLLMENT_TOKEN)
    return rt


# ---------------------------------------------------------------------------- autonomy


def test_standalone_device_works_without_server(rt: Runtime, server: FakeCentralServer):
    assert rt.net.step() > 1
    assert rt.net.state == LinkState.UNCONFIGURED
    rt.emit(5)
    assert rt.store.stats()["events_total"] == 5
    assert rt.store.sync_stats()["pending"] == 0  # nothing queued when there is no server
    assert server.received_batches == 0 and not server.heartbeats


def test_device_identity_is_stable_and_unique(tmp_path: Path, server: FakeCentralServer):
    a = Runtime(tmp_path / "a", server)
    b = Runtime(tmp_path / "b", server)
    first = a.identity.device_id
    a.reopen()
    assert a.identity.device_id == first
    assert b.identity.device_id != first
    a.close()
    b.close()


# ---------------------------------------------------------------------------- registration + sync


def test_registration_heartbeat_and_upload(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    assert rt.net.state == LinkState.ONLINE
    link = rt.links.get()
    assert link and link.has_credentials and link.server_url == SERVER
    # The secret is stored encrypted, never in plain text.
    raw = rt.db.query("SELECT credentials FROM server_link")[0][0]
    assert server.devices[rt.identity.device_id]["secret"].encode() not in raw
    ids = rt.emit(120)
    rt.drain()
    assert set(server.events) == set(ids)
    assert rt.store.sync_stats()["pending"] == 0
    hb = server.heartbeats[0]
    assert hb["device_id"] == rt.identity.device_id and hb["agent_version"]
    assert {"hardware", "metrics", "sync", "cameras"} <= set(hb)
    assert server.received_batches >= 3  # batches of 50


def test_wrong_enrollment_token_is_rejected(rt: Runtime):
    with pytest.raises(RegistrationError):
        rt.net.register(SERVER, "definitely-wrong-token")
    assert rt.net.state == LinkState.ERROR
    assert rt.links.get() is None


def test_auto_registration_from_settings(tmp_path: Path, server: FakeCentralServer):
    r = Runtime(tmp_path / "d", server, url=SERVER, enrollment_token=ENROLLMENT_TOKEN)
    r.net.step()
    assert r.net.state == LinkState.ONLINE and server.registrations == 1
    r.reopen()  # restart: no second registration, stored credentials are used
    r.net.step()
    assert server.registrations == 1 and r.net.state == LinkState.ONLINE
    r.close()


# ---------------------------------------------------------------------------- offline operation


def test_server_down_events_queue_then_sync_after_recovery(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    server.down = True
    ids = rt.emit(30)
    delay = rt.net.step()
    assert rt.net.state == LinkState.OFFLINE
    assert 0 < delay <= 1
    assert rt.store.sync_stats()["pending"] == 30
    ids += rt.emit(20)  # agent keeps recording while offline
    server.down = False
    rt.drain()
    assert rt.net.state == LinkState.ONLINE
    assert set(server.events) == set(ids)  # nothing lost
    assert rt.store.sync_stats()["pending"] == 0


def test_internet_off_backoff_grows_with_jitter(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    rt.emit(1)
    server.down = True
    delays = [rt.net.step() for _ in range(8)]
    assert all(0.05 <= d <= 1.0 for d in delays)  # capped at backoff_max_s
    assert rt.net._backoff.failures == 8


def test_lost_acknowledgement_does_not_duplicate(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    ids = rt.emit(10)
    server.lose_ack = True  # server stores the batch but the reply is lost
    rt.net.step()
    assert rt.net.state == LinkState.OFFLINE
    assert rt.store.sync_stats()["pending"] == 10  # unconfirmed → still queued
    server.lose_ack = False
    rt.drain()
    assert len(server.events) == 10 and set(server.events) == set(ids)  # idempotent by UUID
    assert rt.net.duplicates == 10 and rt.store.sync_stats()["pending"] == 0


def test_rejected_events_leave_queue_and_are_counted(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    ids = rt.emit(5)
    server.reject_ids = {ids[0], ids[1]}
    rt.drain()
    assert rt.net.rejected == 2 and rt.store.sync_stats()["pending"] == 0
    assert rt.store.stats()["events_total"] == 5  # still stored locally


def test_tls_failure_is_treated_as_unreachable(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    rt.emit(3)
    server.tls_error = True
    rt.net.step()
    assert rt.net.state == LinkState.OFFLINE
    assert "CERTIFICATE_VERIFY_FAILED" in (rt.net.last_error or "")
    assert rt.store.sync_stats()["pending"] == 3


def test_plain_http_refused_unless_allowed(tmp_path: Path, server: FakeCentralServer, rt: Runtime):
    with pytest.raises(InsecureServerURL):
        rt.net.register("http://central.example.test", ENROLLMENT_TOKEN)
    with pytest.raises(ValueError):
        rt.net.register("https://user:pw@central.example.test", ENROLLMENT_TOKEN)
    lab = Runtime(tmp_path / "lab", server, allow_insecure_http=True)
    lab.net.register("http://central.example.test", ENROLLMENT_TOKEN)
    assert lab.net.status()["tls"]["https"] is False
    lab.close()


# ---------------------------------------------------------------------------- revocation


def test_revoked_device_stops_sync_and_keeps_working(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    rt.emit(5)
    rt.drain()
    server.revoke_all()
    rt.emit(5)
    rt.net.step()
    assert rt.net.state == LinkState.REVOKED
    link = rt.links.get()
    assert link and link.revoked_at and not link.has_credentials  # credential deleted
    rt.emit(5)  # local recording continues
    assert rt.store.stats()["events_total"] == 15
    rt.reopen()
    rt.net.step()
    assert rt.net.state == LinkState.REVOKED  # survives restart; no retry storm
    # Re-registration with a new enrollment token restores sync, including the backlog.
    server.revoke_all()
    rt.net.register(SERVER, ENROLLMENT_TOKEN)
    rt.drain()
    assert rt.net.state == LinkState.ONLINE


def test_unlink_discards_queue_but_keeps_events(rt: Runtime):
    registered(rt)
    rt.emit(4)
    rt.net.unlink()
    assert rt.links.get() is None and rt.net.state == LinkState.UNCONFIGURED
    assert rt.store.sync_stats()["pending"] == 0 and rt.store.stats()["events_total"] == 4


# ---------------------------------------------------------------------------- configuration


def test_configuration_download_apply_and_ack(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    server.push_config({"device_name": "Gate 3", "ai_enabled": False, "heartbeat_interval_s": 60, "unknown": 1})
    rt.net.step()
    assert rt.identity.device_name == "Gate 3"
    assert rt.applied == {"ai_enabled": False}
    assert rt.net._heartbeat_interval == 60
    ack = server.acks[-1]
    assert ack["version"] == 1 and ack["status"] == "applied" and ack["ignored"] == ["unknown"]
    assert rt.links.get().config_version == 1
    rt.net._next_heartbeat = 0
    rt.net.step()
    assert len(server.acks) == 1  # same version is not downloaded again


def test_invalid_configuration_value_is_reported(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    server.push_config({"heartbeat_interval_s": 1, "device_name": "ok-name"})
    rt.net.step()
    ack = server.acks[-1]
    assert ack["status"] == "partial" and "heartbeat_interval_s" in ack["errors"]
    assert rt.identity.device_name == "ok-name"


# ---------------------------------------------------------------------------- queue, retention, restart


def test_sync_queue_is_bounded(rt: Runtime):
    registered(rt)
    rt.store.settings.sync_queue_max = 10
    rt.emit(25)
    report = rt.store.maintenance()
    assert report["queue_trimmed"] == 15
    s = rt.store.sync_stats()
    assert s["pending"] == 10 and s["dropped"] == 15
    assert rt.store.stats()["events_total"] == 25  # events themselves are kept


def test_retention_keeps_events_waiting_for_upload(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    server.down = True
    old = datetime.now(UTC) - timedelta(days=60)
    rt.emit(3, at=old)
    rt.store.maintenance()
    assert rt.store.stats()["events_total"] == 3  # not uploaded yet → not expired
    server.down = False
    rt.drain()
    rt.store.maintenance()
    assert rt.store.stats()["events_total"] == 0  # uploaded → retention applies


def test_restart_keeps_queue_link_and_credentials(rt: Runtime, server: FakeCentralServer):
    registered(rt)
    server.down = True
    ids = rt.emit(7)
    rt.net.step()
    rt.reopen()
    assert rt.store.sync_stats()["pending"] == 7
    assert rt.links.secret() == server.devices[rt.identity.device_id]["secret"]
    server.down = False
    rt.drain()
    assert set(server.events) == set(ids)


def test_power_loss_snapshot_is_consistent(rt: Runtime, server: FakeCentralServer, tmp_path: Path):
    """Copy the database files while the writer is busy (what a power cut leaves on disk)."""
    registered(rt)
    server.down = True
    for i in range(20):
        rt.store.append(Event(camera_id="cam-1", event_type="motion_started", metadata={"i": i}))
    crash = tmp_path / "crash"
    crash.mkdir()
    for name in ("events.db", "events.db-wal"):
        src = rt.data / name
        if src.exists():
            shutil.copy2(src, crash / name)
    rt.emit(5)
    db = Database(crash / "events.db", migrations=[])  # opens + replays WAL like a reboot
    assert db.integrity_ok()
    orphans = db.query("SELECT COUNT(*) FROM sync_queue WHERE event_id NOT IN (SELECT id FROM events)")[0][0]
    unqueued = db.query("SELECT COUNT(*) FROM events WHERE id NOT IN (SELECT event_id FROM sync_queue)")[0][0]
    assert orphans == 0 and unqueued == 0  # event + queue entry are written atomically
    db.close()


def test_corrupt_event_database_recovers_and_sync_continues(tmp_path: Path, server: FakeCentralServer):
    r = Runtime(tmp_path / "c", server)
    registered(r)
    r.close()
    (tmp_path / "c" / "events.db").write_bytes(b"this is not a database" * 100)
    for suffix in ("-wal", "-shm"):
        (tmp_path / "c" / f"events.db{suffix}").unlink(missing_ok=True)
    r._open()
    assert r.store.recoveries  # quarantined + recreated
    ids = r.emit(4)
    r.drain()
    assert set(ids) <= set(server.events)
    r.close()


# ---------------------------------------------------------------------------- backups, system log, backoff


def test_backup_and_restore(rt: Runtime, tmp_path: Path):
    rt.emit(3)
    result = create_backup({"agent": rt.db, "events": rt.store.db}, rt.data / "backups", keep=2)
    assert set(result["files"]) == {"agent", "events"}
    for _ in range(2):
        create_backup({"agent": rt.db, "events": rt.store.db}, rt.data / "backups", keep=2)
    assert len(list_backups(rt.data / "backups")) == 2  # pruned to keep
    device_id = rt.identity.device_id
    rt.emit(10)
    rt.close()
    restore_backup(Path(list_backups(rt.data / "backups")[-1]["path"]), rt.data)
    rt._open()
    assert rt.identity.device_id == device_id
    assert rt.store.stats()["events_total"] == 3
    assert (rt.data / "events.db.before-restore").exists()


def test_system_log_is_bounded_and_redacted(rt: Runtime):
    handler = SystemLogHandler(rt.db, "INFO", max_rows=150)
    log = logging.getLogger("aci.agent.test-runtime")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        log.warning("Camera rtsp://admin:hunter2@10.0.0.5/stream password=hunter2 failed")
        for i in range(400):
            log.info("tick %d", i)
    finally:
        log.removeHandler(handler)
        handler.close()
    rows = read_logs(rt.db, limit=1000)
    assert len(rows) <= 150 + 200  # trimmed in steps of ≥100 rows
    warn = read_logs(rt.db, level="WARNING")
    assert all("hunter2" not in r["message"] for r in rt.db.query("SELECT message FROM system_logs"))
    assert all(r["level"] in ("WARNING", "ERROR", "CRITICAL") for r in warn)


def test_backoff_is_bounded_and_resets():
    b = Backoff(1, 30)
    delays = [b.next_delay() for _ in range(20)]
    assert all(0.5 <= d <= 30 for d in delays)
    assert b.next_delay(minimum=120) == 30  # Retry-After honoured but capped
    b.reset()
    assert b.failures == 0 and b.next_delay() <= 1


def test_database_snapshot_survives_sqlite_open(tmp_path: Path):
    """Backups are plain SQLite files that open with the stock sqlite3 module (manual recovery)."""
    db = Database(tmp_path / "agent.db")
    db.backup_to(tmp_path / "copy.db")
    conn = sqlite3.connect(tmp_path / "copy.db")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    db.close()
