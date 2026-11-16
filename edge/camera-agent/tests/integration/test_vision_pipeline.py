"""End-to-end Standard Vision Engine: capture worker → vision worker → local event database.

Covers the MVP 03 test list: offline (no internet / server / AI), single and multiple moving
objects, noise, lighting change, stream loss and camera recovery, frame skipping, memory limit.
"""

from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from aci_agent.camera.manager import CameraInput, CameraManager
from aci_agent.camera.models import CameraStatus, SourceType
from aci_agent.events.store import EventStore, EventStoreSettings
from aci_agent.standard_engine.config import VisionConfig
from aci_agent.standard_engine.pipeline import VisionManager
from aci_agent.storage.vision_repository import VisionRepository
from tests.conftest import FakeFactory, wait_for
from tests.vision_scenes import moving_box_scene, static_scene, two_boxes_scene

URL = "rtsp://10.0.5.1/live"


@pytest.fixture
def store(tmp_path) -> Iterator[EventStore]:
    s = EventStore(tmp_path / "events.db", EventStoreSettings(initial_maintenance_delay_s=3600, min_free_disk_mb=50))
    yield s
    s.close()


@pytest.fixture
def vision(manager: CameraManager, store: EventStore) -> Iterator[VisionManager]:
    vm = VisionManager(manager, VisionRepository(manager.repo.db), store, supervisor_interval_s=0.2)
    yield vm
    vm.shutdown()


def _camera(manager: CameraManager, factory: FakeFactory, scene, fps: float = 25.0, url: str = URL):
    src = factory.add(url, width=640, height=360, fps=fps)
    src.scene = scene
    cam = manager.create_camera(CameraInput(name="Cam", source_type=SourceType.RTSP, source_uri=url)).config
    return cam, src


def _events(store: EventStore, cam_id: str, kind: str | None = None) -> list[dict]:
    store.flush()
    return store.query(camera_id=cam_id, event_types=[kind] if kind else None, limit=1000)


def _configure(vision: VisionManager, cam_id: str, **cfg) -> None:
    vision.repo.save_config(cam_id, VisionConfig(**cfg))
    vision.sync()
    vision.reload(cam_id)


def test_motion_started_and_ended_for_a_moving_object(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, moving_box_scene(speed=8, start_frame=20, end_frame=70))
    _configure(vision, cam.id, process_fps=25, motion_end_delay_s=0.5)
    vision.start()
    assert wait_for(lambda: _events(store, cam.id, "motion_started"), timeout=10)
    assert wait_for(lambda: _events(store, cam.id, "motion_ended"), timeout=10)
    started = _events(store, cam.id, "motion_started")[-1]
    ended = _events(store, cam.id, "motion_ended")[-1]
    assert started["source"] == "standard" and started["metadata"]["objects"] >= 1
    assert ended["metadata"]["duration_s"] > 0.5 and ended["metadata"]["reason"] == "no motion"
    assert len(_events(store, cam.id, "motion_started")) == 1  # one episode, not one per frame


def test_line_crossing_and_zone_events(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, moving_box_scene(speed=8, start_frame=20, end_frame=95))
    repo = vision.repo
    line = repo.save_line(cam.id, "Door", (0.5, 0.05), (0.5, 0.95), True)
    zone = repo.save_zone(cam.id, "Gate", "monitor", [(0.6, 0.0), (0.85, 0.0), (0.85, 1.0), (0.6, 1.0)], True)
    _configure(vision, cam.id, process_fps=25, motion_end_delay_s=0.5)
    vision.start()
    assert wait_for(lambda: _events(store, cam.id, "line_crossed"), timeout=12)
    assert wait_for(lambda: _events(store, cam.id, "object_exited_zone"), timeout=12)
    crossed = _events(store, cam.id, "line_crossed")
    assert len(crossed) == 1 and crossed[0]["line_id"] == line["id"]
    assert crossed[0]["metadata"]["direction"] == "forward"  # moving right across a top→bottom line
    assert crossed[0]["metadata"]["heading"] == "E" and crossed[0]["track_id"].startswith("m-")
    entered = _events(store, cam.id, "object_entered_zone")
    assert entered and entered[0]["zone_id"] == zone["id"]
    assert store.line_counts(cam.id, datetime(2000, 1, 1, tzinfo=UTC)) == {line["id"]: {"forward": 1, "backward": 0}}


def test_multiple_objects_get_separate_tracks(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, two_boxes_scene(start_frame=20))
    _configure(vision, cam.id, process_fps=25)
    vision.start()
    assert wait_for(lambda: len(vision.state(cam.id).tracks) >= 2, timeout=10)
    ids = {t.track_id for t in vision.state(cam.id).tracks}
    assert len(ids) >= 2


def test_noise_only_scene_produces_no_events(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, static_scene(noise=12))
    _configure(vision, cam.id, process_fps=25)
    vision.start()
    assert wait_for(lambda: vision.state(cam.id).frames_analysed > 60, timeout=10)
    assert _events(store, cam.id) == []


def test_works_offline_without_ai_or_server(manager, factory, vision, store, monkeypatch):
    """No network at all: any socket connection attempt fails. No AI framework is imported."""

    def no_network(*args, **kwargs):
        raise OSError("network disabled for this test")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    cam, _ = _camera(manager, factory, moving_box_scene(speed=8, start_frame=20))
    _configure(vision, cam.id, process_fps=25)
    vision.start()
    assert wait_for(lambda: _events(store, cam.id, "motion_started"), timeout=10)
    for module in ("torch", "tensorflow", "ultralytics", "onnxruntime"):
        assert module not in sys.modules


def test_stream_loss_closes_motion_and_recovery_resumes(manager, factory, vision, store):
    cam, src = _camera(manager, factory, moving_box_scene(speed=4, start_frame=20))
    _configure(vision, cam.id, process_fps=25, motion_end_delay_s=30)  # long debounce: only stream loss ends it
    vision.start()
    assert wait_for(lambda: vision.state(cam.id).motion_active, timeout=10)
    src.cable_connected = False  # stream lost mid-motion
    assert wait_for(lambda: _events(store, cam.id, "motion_ended"), timeout=10)
    assert _events(store, cam.id, "motion_ended")[0]["metadata"]["reason"] in ("no video frames", "camera stopped")
    assert wait_for(lambda: vision.state(cam.id).status == "waiting_for_video", timeout=5)
    assert vision.state(cam.id).stream_interruptions >= 1
    src.cable_connected = True  # camera recovers
    assert wait_for(lambda: manager.get_camera(cam.id).runtime.status == CameraStatus.ONLINE, timeout=8)
    assert wait_for(lambda: len(_events(store, cam.id, "motion_started")) >= 2, timeout=12)


def test_frame_skipping_respects_process_fps(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, static_scene(), fps=40)
    _configure(vision, cam.id, process_fps=4)
    vision.start()
    assert wait_for(lambda: vision.state(cam.id).frames_analysed >= 8, timeout=10)
    st = vision.state(cam.id)
    assert st.analysis_fps is not None and st.analysis_fps < 6
    assert st.frames_skipped > st.frames_analysed  # most frames are skipped, capture is unaffected
    assert manager.get_camera(cam.id).runtime.status == CameraStatus.ONLINE


def test_memory_limit_pauses_analysis_but_not_capture(manager, factory, store):
    vm = VisionManager(manager, VisionRepository(manager.repo.db), store, max_rss_mb=1, supervisor_interval_s=0.1)
    cam, _ = _camera(manager, factory, moving_box_scene(speed=8))
    vm.start()
    try:
        assert wait_for(lambda: vm.memory_paused, timeout=5)
        assert wait_for(lambda: vm.state(cam.id).status == "paused_memory", timeout=5)
        assert manager.get_camera(cam.id).runtime.status == CameraStatus.ONLINE
        vm.max_rss_mb = 1_000_000  # memory available again
        assert wait_for(lambda: not vm.memory_paused and vm.state(cam.id).status in ("active", "warming_up"), 5)
    finally:
        vm.shutdown()


def test_vision_disabled_per_camera(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, moving_box_scene(speed=8))
    _configure(vision, cam.id, enabled=False)
    vision.start()
    assert wait_for(lambda: vision.state(cam.id).status == "disabled", timeout=5)
    assert _events(store, cam.id) == []


def test_deleting_camera_stops_its_vision_worker(manager, factory, vision, store):
    cam, _ = _camera(manager, factory, static_scene())
    vision.start()
    assert wait_for(lambda: vision.worker(cam.id) is not None, timeout=5)
    worker = vision.worker(cam.id)
    manager.delete_camera(cam.id)
    assert wait_for(lambda: vision.worker(cam.id) is None and not worker.is_alive(), timeout=5)


def test_shutdown_ends_open_motion_episode(manager, factory, store):
    vm = VisionManager(manager, VisionRepository(manager.repo.db), store, supervisor_interval_s=0.2)
    cam, _ = _camera(manager, factory, moving_box_scene(speed=3, start_frame=20))
    vm.repo.save_config(cam.id, VisionConfig(process_fps=25, motion_end_delay_s=60))
    vm.start()
    assert wait_for(lambda: vm.state(cam.id) and vm.state(cam.id).motion_active, timeout=10)
    vm.shutdown()
    ended = _events(store, cam.id, "motion_ended")
    assert ended and ended[0]["metadata"]["reason"] == "vision stopped"
