import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aci_agent.events.models import Event
from aci_agent.events.store import EventStore, EventStoreSettings


def _store(tmp_path: Path, **kw) -> EventStore:
    kw.setdefault("maintenance_interval_s", 3600)
    kw.setdefault("initial_maintenance_delay_s", 3600)
    kw.setdefault("min_free_disk_mb", 50)  # tests must not depend on the machine's free space
    return EventStore(tmp_path / "events.db", EventStoreSettings(**kw))


def test_events_are_persisted_with_source_uuid(tmp_path: Path):
    s = _store(tmp_path)
    e = Event(camera_id="cam1", event_type="motion_started", metadata={"objects": 2})
    assert s.append(e)
    s.flush()
    rows = s.query(camera_id="cam1")
    assert rows[0]["id"] == e.id and rows[0]["metadata"] == {"objects": 2} and rows[0]["source"] == "standard"
    s.close()


def test_duplicate_event_id_is_ignored(tmp_path: Path):
    s = _store(tmp_path)
    e = Event(camera_id="cam1", event_type="motion_started")
    s.append(e)
    s.append(e)
    s.flush()
    assert s.stats()["events_total"] == 1
    s.close()


def test_filters_and_keyset_pagination(tmp_path: Path):
    s = _store(tmp_path)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(25):
        s.append(Event(camera_id="a" if i % 2 else "b", event_type="line_crossed" if i % 3 == 0 else "motion_started",
                       occurred_at=base + timedelta(seconds=i)))  # fmt: skip
    s.flush()
    page1 = s.query(limit=10)
    page2 = s.query(limit=10, before=(page1[-1]["occurred_at"], page1[-1]["id"]))
    assert len(page1) == len(page2) == 10
    assert page1[-1]["occurred_at"] > page2[0]["occurred_at"]
    assert all(r["camera_id"] == "a" for r in s.query(camera_id="a"))
    assert all(r["event_type"] == "line_crossed" for r in s.query(event_types=["line_crossed"]))
    assert len(s.query(since=base + timedelta(seconds=20))) == 5
    s.close()


def test_retention_deletes_old_events(tmp_path: Path):
    s = _store(tmp_path, retention_days=7)
    s.append(Event(camera_id="c", event_type="motion_started", occurred_at=datetime.now(UTC) - timedelta(days=30)))
    s.append(Event(camera_id="c", event_type="motion_started"))
    s.flush()
    report = s.maintenance()
    assert report["expired"] == 1 and s.stats()["events_total"] == 1
    s.close()


def test_size_limit_prunes_oldest_once(tmp_path: Path):
    s = _store(tmp_path, max_db_mb=16)
    for i in range(200):
        s.append(Event(camera_id="c", event_type="motion_started", metadata={"pad": "x" * 2000, "i": i}))
    s.flush()
    s.settings.max_db_mb = 0  # pretend the limit is exceeded
    first = s.maintenance()
    assert first["pruned_size"] == 20  # oldest 10 %
    s.settings.max_db_mb = 16
    assert s.maintenance()["pruned_size"] == 0  # freed pages are not counted as used → no repeat pruning
    s.close()


def test_low_disk_prunes_and_pauses_then_resumes(tmp_path: Path):
    free = {"mb": 10_000}
    s = EventStore(
        tmp_path / "events.db",
        EventStoreSettings(min_free_disk_mb=500, maintenance_interval_s=3600, initial_maintenance_delay_s=3600),
        disk_usage=lambda _: (0, 0, int(free["mb"] * 1_048_576)),
    )
    for _ in range(10):
        s.append(Event(camera_id="c", event_type="motion_started"))
    s.flush()
    free["mb"] = 100  # critically low
    report = s.maintenance()
    assert report["pruned_disk"] >= 1 and s.storage_paused
    assert not s.append(Event(camera_id="c", event_type="motion_started"))  # writing paused, counted
    assert s.dropped == 1
    free["mb"] = 5_000
    s.maintenance()
    assert not s.storage_paused and s.append(Event(camera_id="c", event_type="motion_started"))
    s.close()


def test_full_queue_drops_instead_of_blocking(tmp_path: Path):
    s = _store(tmp_path, queue_size=100)
    gate = threading.Lock()
    gate.acquire()
    s._lock = gate  # writer can't write → queue fills up
    started = time.perf_counter()
    results = [s.append(Event(camera_id="c", event_type="motion_started")) for _ in range(500)]
    assert time.perf_counter() - started < 1.0  # never blocks the caller
    assert results.count(False) >= 300 and s.dropped >= 300
    gate.release()
    s._lock = threading.Lock()
    s.close()


def test_safe_shutdown_flushes_pending_events(tmp_path: Path):
    s = _store(tmp_path)
    for _ in range(300):
        s.append(Event(camera_id="c", event_type="motion_started"))
    s.close()  # must drain the queue before closing
    s2 = _store(tmp_path)
    assert s2.stats()["events_total"] == 300
    assert not Path(str(tmp_path / "events.db") + "-wal").exists() or (tmp_path / "events.db-wal").stat().st_size == 0
    s2.close()


def test_corrupt_database_is_quarantined_and_recreated(tmp_path: Path):
    path = tmp_path / "events.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    s = EventStore(path, EventStoreSettings(maintenance_interval_s=3600, initial_maintenance_delay_s=3600))
    assert s.recoveries and "not a database" in s.recoveries[0] or s.recoveries
    assert list(tmp_path.glob("events.db.corrupt-*"))  # original kept for manual recovery
    assert s.append(Event(camera_id="c", event_type="motion_started"))
    s.flush()
    assert s.stats()["events_total"] == 1 and s.integrity_check()
    s.close()


def test_line_counts_and_summary(tmp_path: Path):
    s = _store(tmp_path)
    for d in ["forward", "forward", "backward"]:
        s.append(Event(camera_id="c", event_type="line_crossed", line_id="L1", metadata={"direction": d}))
    s.append(Event(camera_id="c", event_type="motion_started"))
    s.flush()
    since = datetime.now(UTC) - timedelta(hours=1)
    assert s.line_counts("c", since) == {"L1": {"forward": 2, "backward": 1}}
    assert s.counts_by_type(since) == {"line_crossed": 3, "motion_started": 1}
    s.close()
