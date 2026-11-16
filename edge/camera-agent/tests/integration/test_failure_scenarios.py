"""MVP 02 test matrix: connection, wrong URL, wrong password, unplugged cable, network loss and
recovery, invalid video format, concurrent cameras, resource release."""

from __future__ import annotations

import threading
import time

import pytest

from aci_agent.camera.manager import CameraInput, CameraManager
from aci_agent.camera.models import CameraStatus, FailureKind, SourceType
from aci_agent.camera.rtsp_probe import ProbeResult
from tests.conftest import FakeFactory, wait_for

URL_A = "rtsp://10.0.0.11:554/live"
URL_B = "rtsp://10.0.0.12:554/live"


def _add(manager: CameraManager, url: str, name: str = "Cam", **kw):
    return manager.create_camera(CameraInput(name=name, source_type=SourceType.RTSP, source_uri=url, **kw)).config


def _status(manager: CameraManager, cid: str):
    return manager.get_camera(cid).runtime


def test_camera_connects_and_reports_real_stream_state(manager: CameraManager, factory: FakeFactory):
    factory.add(URL_A, width=320, height=240, fps=40)
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
    assert wait_for(lambda: (_status(manager, cam.id).fps or 0) > 10)
    rt = _status(manager, cam.id)
    assert (rt.width, rt.height) == (320, 240)
    assert rt.last_connected_at is not None and rt.last_frame_at is not None
    assert manager.frame_buffer(cam.id).latest() is not None


def test_invalid_url_is_rejected_before_saving(manager: CameraManager):
    with pytest.raises(ValueError):
        _add(manager, "http://not-rtsp/stream")
    assert manager.repo.count() == 0


def test_unreachable_host_retries_with_backoff(manager: CameraManager, factory: FakeFactory, probe_results):
    probe_results[URL_A] = ProbeResult(False, FailureKind.UNREACHABLE, "No response from 10.0.0.11:554 (timeout)")
    cam = _add(manager, URL_A)  # no fake source registered → open fails
    assert wait_for(lambda: _status(manager, cam.id).reconnect_attempts >= 2, timeout=5)
    rt = _status(manager, cam.id)
    assert rt.status == CameraStatus.RECONNECTING
    assert rt.failure_kind == FailureKind.UNREACHABLE


def test_wrong_password_is_an_error_not_online(manager: CameraManager, factory: FakeFactory, probe_results):
    src = factory.add(URL_A)
    src.cable_connected = False  # FFmpeg refuses to open → agent diagnoses why
    probe_results[URL_A] = ProbeResult(False, FailureKind.AUTH_FAILED, "Wrong username or password", 401)
    cam = _add(manager, URL_A, username="admin", password="wrong-pass")
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ERROR)
    rt = _status(manager, cam.id)
    assert rt.failure_kind == FailureKind.AUTH_FAILED
    assert "wrong-pass" not in (rt.last_error or "")


def test_cable_unplug_then_replug_recovers(manager: CameraManager, factory: FakeFactory):
    src = factory.add(URL_A)
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
    src.cable_connected = False  # cable pulled: reads fail, re-open fails
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.RECONNECTING)
    assert _status(manager, cam.id).failure_kind in (FailureKind.STREAM_LOST, FailureKind.UNREACHABLE)
    src.cable_connected = True  # network restored
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE, timeout=6)
    assert _status(manager, cam.id).reconnect_attempts == 0


def test_stalled_stream_is_not_reported_online(manager: CameraManager, factory: FakeFactory):
    src = factory.add(URL_A)
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
    src.unblocked.clear()  # decoder hangs: socket "open", no frames
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.RECONNECTING, timeout=4)
    assert "No frames" in _status(manager, cam.id).last_error
    src.unblocked.set()
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE, timeout=4)


def test_undecodable_stream_reports_unsupported_format(manager: CameraManager, factory: FakeFactory):
    src = factory.add(URL_A)
    src.decodable = False
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ERROR)
    assert _status(manager, cam.id).failure_kind == FailureKind.UNSUPPORTED_FORMAT


def test_one_failing_camera_does_not_affect_others(manager: CameraManager, factory: FakeFactory):
    good = factory.add(URL_A, fps=30)
    bad = factory.add(URL_B)
    a = _add(manager, URL_A, name="Good")
    b = _add(manager, URL_B, name="Bad")
    assert wait_for(lambda: _status(manager, a.id).status == CameraStatus.ONLINE)
    assert wait_for(lambda: _status(manager, b.id).status == CameraStatus.ONLINE)
    frames_before = _status(manager, a.id).frames_received
    bad.cable_connected = False
    assert wait_for(lambda: _status(manager, b.id).status == CameraStatus.RECONNECTING)
    time.sleep(0.5)
    rt_a = _status(manager, a.id)
    assert rt_a.status == CameraStatus.ONLINE
    assert rt_a.frames_received > frames_before  # still streaming
    assert good.open_devices == 1


def test_many_cameras_have_independent_workers(manager: CameraManager, factory: FakeFactory):
    urls = [f"rtsp://10.0.1.{i}:554/live" for i in range(1, 9)]
    ids = []
    for i, url in enumerate(urls):
        factory.add(url, fps=25)
        ids.append(_add(manager, url, name=f"Cam {i}").id)
    assert wait_for(lambda: all(_status(manager, c).status == CameraStatus.ONLINE for c in ids), timeout=8)
    names = {t.name for t in threading.enumerate() if t.name.startswith("camera-")}
    assert len(names) == len(ids)
    assert manager.active_workers() == len(ids)


def test_disconnect_releases_resources(manager: CameraManager, factory: FakeFactory):
    src = factory.add(URL_A)
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
    manager.disconnect(cam.id)
    assert _status(manager, cam.id).status == CameraStatus.OFFLINE
    assert src.open_devices == 0  # capture device released
    assert manager.frame_buffer(cam.id) is None
    assert not any(t.name == f"camera-{cam.id[:8]}" and t.is_alive() for t in threading.enumerate())
    assert manager.leaked_workers == 0


def test_reconnect_cycles_do_not_leak_devices(manager: CameraManager, factory: FakeFactory):
    src = factory.add(URL_A)
    cam = _add(manager, URL_A)
    for _ in range(3):
        assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
        src.cable_connected = False
        assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.RECONNECTING)
        src.cable_connected = True
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE, timeout=6)
    assert src.open_devices == 1  # every previous device was released
    manager.delete_camera(cam.id)
    assert src.open_devices == 0


def test_disable_and_enable(manager: CameraManager, factory: FakeFactory):
    factory.add(URL_A)
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
    data = CameraInput(name="Cam", source_type=SourceType.RTSP, source_uri=URL_A, is_enabled=False)
    manager.update_camera(cam.id, data)
    assert _status(manager, cam.id).status == CameraStatus.DISABLED
    data.is_enabled = True
    manager.update_camera(cam.id, data)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)


def test_slow_consumer_cannot_grow_memory(manager: CameraManager, factory: FakeFactory):
    factory.add(URL_A, fps=100)
    cam = _add(manager, URL_A)
    assert wait_for(lambda: _status(manager, cam.id).status == CameraStatus.ONLINE)
    queue = manager.subscribe(cam.id, maxsize=3)
    time.sleep(0.5)  # never consume
    assert len(queue) == 3
    assert queue.dropped > 10


def test_restart_restores_cameras_from_local_db(settings, factory: FakeFactory):
    """Agent restart: cameras and encrypted credentials come back from SQLite and reconnect."""
    from aci_agent.security.crypto import SecretBox
    from aci_agent.storage.database import Database
    from aci_agent.storage.repository import CameraRepository

    def build():
        db = Database(settings.database_path)
        box = SecretBox.from_settings("", settings.secret_key_path)
        return CameraManager(CameraRepository(db, box), settings, factory=factory), db

    factory.add(URL_A)
    m1, db1 = build()
    cam = m1.create_camera(
        CameraInput(name="Gate", source_type=SourceType.RTSP, source_uri=URL_A, username="admin", password="pw")
    ).config
    m1.shutdown()
    db1.close()

    m2, db2 = build()
    m2.start()
    try:
        assert wait_for(lambda: m2.get_camera(cam.id).runtime.status == CameraStatus.ONLINE)
        assert m2.repo.get_secret(cam.id).password == "pw"
    finally:
        m2.shutdown()
        db2.close()
