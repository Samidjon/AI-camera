"""MVP 04 test list: start without AI, start with AI, disable AI while running, corrupted model,
unavailable GPU, insufficient memory, inference timeout, AI recovery, Standard Engine keeps working.

The AI provider is a controllable fake (real-model tests are in tests/unit/test_ai_components.py);
capture, Standard Vision Engine and event storage are the real ones.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from aci_agent.ai_engine.attributes.claude_provider import ClaudeAttributeProvider
from aci_agent.ai_engine.configuration import AIEnvSettings, AIRuntimeConfig, AttributeProviderKind
from aci_agent.ai_engine.engine import AIEngine, AIMode
from aci_agent.camera.manager import CameraInput, CameraManager
from aci_agent.camera.models import CameraStatus, SourceType
from aci_agent.events.store import EventStore, EventStoreSettings
from aci_agent.standard_engine.config import VisionConfig
from aci_agent.standard_engine.pipeline import VisionManager
from aci_agent.storage.ai_repository import AIRepository
from aci_agent.storage.vision_repository import VisionRepository
from tests.ai_fakes import FakeAIProvider, FakeClaudeClient, fake_registry
from tests.conftest import FakeFactory, wait_for
from tests.vision_scenes import moving_box_scene

URL = "rtsp://10.0.9.1/live"


@pytest.fixture
def store(tmp_path) -> Iterator[EventStore]:
    s = EventStore(tmp_path / "events.db", EventStoreSettings(initial_maintenance_delay_s=3600, min_free_disk_mb=50))
    yield s
    s.close()


class Rig:
    def __init__(self, manager: CameraManager, factory: FakeFactory, store: EventStore, tmp_path: Path) -> None:
        self.manager, self.store = manager, store
        self.behaviour: dict = {}
        self.memory = {"available": 8000.0, "rss": 200.0}
        self.registry, self.model_path = fake_registry(tmp_path)
        src = factory.add(URL, width=640, height=360, fps=25)
        src.scene = moving_box_scene(speed=6, start_frame=20)
        self.src = src
        self.camera = manager.create_camera(
            CameraInput(name="Door", source_type=SourceType.RTSP, source_uri=URL)
        ).config
        self.vision_repo = VisionRepository(manager.repo.db)
        self.vision_repo.save_config(self.camera.id, VisionConfig(process_fps=25, motion_end_delay_s=0.5))
        self.vision = VisionManager(manager, self.vision_repo, store, supervisor_interval_s=0.2)
        self.claude_client = FakeClaudeClient()
        self.ai = AIEngine(
            manager, store, AIRepository(manager.repo.db), self.vision_repo,
            env=AIEnvSettings(_env_file=None, inference_timeout_s=0.4, recovery_initial_s=0.3, recovery_max_s=1.0,
                              min_free_memory_mb=256, max_rss_mb=4096, api_key="sk-ant-test"),
            registry=self.registry,
            provider_factory=lambda env, cfg: FakeAIProvider(self.behaviour),
            memory_available_mb=lambda: self.memory["available"],
            rss_mb=lambda: self.memory["rss"],
            claude_factory=lambda **kw: ClaudeAttributeProvider(client_factory=lambda k, t: self.claude_client, **kw),
        )  # fmt: skip
        self.vision.start()
        self.ai.start()

    def events(self, *, source: str | None = None, kind: str | None = None) -> list[dict]:
        self.store.flush()
        rows = self.store.query(camera_id=self.camera.id, event_types=[kind] if kind else None, limit=1000)
        return [r for r in rows if source is None or r["source"] == source]

    def enable(self, **cfg) -> None:
        if cfg:
            self.ai.update_config(AIRuntimeConfig(**{**self.ai.config.model_dump(), **cfg}))
        self.ai.set_enabled(True)

    def close(self) -> None:
        self.ai.shutdown()
        self.vision.shutdown()


@pytest.fixture
def rig(manager, factory, store, tmp_path) -> Iterator[Rig]:
    r = Rig(manager, factory, store, tmp_path)
    yield r
    r.close()


def _mode(rig: Rig) -> AIMode:
    return rig.ai.mode


def test_starts_without_ai_and_standard_engine_works(rig: Rig):
    assert wait_for(lambda: rig.events(kind="motion_started"), timeout=10)
    assert _mode(rig) == AIMode.DISABLED and rig.ai.status()["fallback"] == "standard_engine"
    assert rig.events(source="ai") == []
    assert FakeAIProvider.instances == [] or all(not p.calls for p in FakeAIProvider.instances)


def test_starts_with_ai_and_tracks_people(rig: Rig):
    rig.enable(process_fps=10, min_track_hits=2)
    assert wait_for(lambda: _mode(rig) == AIMode.ACTIVE, timeout=5)
    assert wait_for(lambda: rig.events(source="ai", kind="person_detected"), timeout=8)
    ev = rig.events(source="ai", kind="person_detected")[0]
    assert ev["track_id"].startswith("p-") and ev["metadata"]["confidence"] >= 0.9
    assert "attributes" in ev["metadata"]
    rig.behaviour["person"] = False  # person leaves
    assert wait_for(lambda: rig.events(source="ai", kind="person_left"), timeout=8)
    left = rig.events(source="ai", kind="person_left")[0]
    assert left["metadata"]["reason"] == "left view" and left["metadata"]["duration_s"] > 0
    m = rig.ai.status()["metrics"]
    assert m["inferences"] > 5 and m["latency_avg_ms"] is not None
    assert rig.events(source="standard", kind="motion_started")  # both engines produce events


def test_disable_ai_while_running(rig: Rig):
    rig.enable(min_track_hits=2)
    assert wait_for(lambda: rig.events(source="ai", kind="person_detected"), timeout=8)
    rig.ai.set_enabled(False)
    assert wait_for(lambda: _mode(rig) == AIMode.DISABLED, timeout=5)
    left = rig.events(source="ai", kind="person_left")
    assert left and left[0]["metadata"]["reason"] == "AI disabled"
    before = len(rig.events(source="standard"))
    calls = sum(p.calls for p in FakeAIProvider.instances)
    assert wait_for(lambda: len(rig.events(source="standard")) > before, timeout=10)  # Standard continues
    assert sum(p.calls for p in FakeAIProvider.instances) == calls  # no more inference
    assert rig.manager.get_camera(rig.camera.id).runtime.status == CameraStatus.ONLINE


def test_corrupted_model_goes_offline_without_affecting_capture(rig: Rig):
    rig.model_path.write_bytes(b"corrupted" * 10)
    rig.enable()
    assert wait_for(lambda: _mode(rig) == AIMode.OFFLINE, timeout=5)
    assert "Size" in rig.ai.mode_reason or "SHA-256" in rig.ai.mode_reason
    assert rig.ai.registry.get("yolox_s_coco_2022nov").status.value == "invalid"
    assert wait_for(lambda: rig.events(source="standard", kind="motion_started"), timeout=10)


def test_model_that_fails_to_load_is_error_then_recovers_when_fixed(rig: Rig):
    rig.behaviour["fail_load"] = True
    rig.enable()
    assert wait_for(lambda: _mode(rig) == AIMode.ERROR, timeout=5)
    assert any(e["kind"] == "model" for e in rig.ai.errors)
    rig.behaviour["fail_load"] = False
    assert wait_for(lambda: _mode(rig) == AIMode.ACTIVE, timeout=8)


def test_unavailable_gpu_runs_degraded_on_cpu(rig: Rig):
    rig.behaviour["warnings"] = ["GPU requested but not available to OpenCV — running on CPU"]
    rig.enable()
    assert wait_for(lambda: _mode(rig) == AIMode.DEGRADED, timeout=5)
    assert "GPU" in rig.ai.mode_reason
    assert wait_for(lambda: rig.ai.status()["metrics"]["inferences"] > 3, timeout=5)  # still inferring
    assert rig.ai.status()["resources"]["gpu"]["available"] in (True, False)


def test_insufficient_memory_refuses_to_load(rig: Rig):
    rig.memory["available"] = 100.0
    rig.enable()
    assert wait_for(lambda: _mode(rig) == AIMode.ERROR, timeout=5)
    assert "Insufficient memory" in rig.ai.mode_reason
    assert wait_for(lambda: rig.events(source="standard", kind="motion_started"), timeout=10)
    rig.memory["available"] = 8000.0
    assert wait_for(lambda: _mode(rig) == AIMode.ACTIVE, timeout=8)  # recovers by itself


def test_memory_limit_while_running(rig: Rig):
    rig.enable()
    assert wait_for(lambda: _mode(rig) == AIMode.ACTIVE, timeout=5)
    rig.memory["rss"] = 10_000.0
    assert wait_for(lambda: _mode(rig) == AIMode.ERROR, timeout=5)
    assert "memory" in rig.ai.mode_reason.lower()


def test_inference_timeout_is_contained_and_recovers(rig: Rig):
    rig.behaviour["delay_s"] = 2.0  # hung model
    rig.enable()
    assert wait_for(lambda: rig.ai.metrics.timeouts >= 1, timeout=8)
    assert _mode(rig) in (AIMode.ERROR, AIMode.RECOVERING, AIMode.ACTIVE)
    assert any(e["kind"] == "timeout" for e in rig.ai.errors)
    # capture and Standard Engine were never blocked by the hung call
    assert rig.manager.get_camera(rig.camera.id).runtime.status == CameraStatus.ONLINE
    assert wait_for(lambda: rig.events(source="standard", kind="motion_started"), timeout=10)
    rig.behaviour["delay_s"] = 0
    assert wait_for(lambda: _mode(rig) == AIMode.ACTIVE and rig.ai.metrics.snapshot()["inference_fps"], 10)


def test_ai_crash_recovers_with_backoff_and_history(rig: Rig):
    rig.behaviour["raise_predict"] = 2  # two crashes, then healthy
    rig.enable()
    assert wait_for(lambda: rig.ai.metrics.errors >= 2, timeout=10)
    assert wait_for(lambda: _mode(rig) == AIMode.ACTIVE, timeout=10)
    assert "Recovered" in rig.ai.mode_reason
    modes = [h["mode"] for h in rig.ai._repo.history(50)]
    assert "error" in modes and "recovering" in modes and modes[0] == "active"
    assert rig.manager.get_camera(rig.camera.id).runtime.status == CameraStatus.ONLINE


def test_camera_loss_ends_person_tracks(rig: Rig):
    rig.enable(min_track_hits=2)
    assert wait_for(lambda: rig.events(source="ai", kind="person_detected"), timeout=8)
    rig.src.cable_connected = False
    assert wait_for(lambda: any(e["metadata"]["reason"] in ("camera stream lost", "camera stream stopped")
                                for e in rig.events(source="ai", kind="person_left")), timeout=10)  # fmt: skip
    assert _mode(rig) == AIMode.ACTIVE  # a camera problem is not an AI failure


def test_person_line_crossing_uses_the_same_lines(rig: Rig):
    rig.vision_repo.save_line(rig.camera.id, "Door", (0.5, 0.05), (0.5, 0.95), True)
    rig.enable(min_track_hits=2, process_fps=12)
    assert wait_for(lambda: rig.events(source="ai", kind="person_crossed_line"), timeout=12)
    ev = rig.events(source="ai", kind="person_crossed_line")[0]
    assert ev["metadata"]["direction"] == "forward" and ev["line_id"]


def test_claude_attributes_are_merged_into_person_events(rig: Rig):
    rig.enable(min_track_hits=2, attributes=AttributeProviderKind.CLAUDE)
    assert wait_for(lambda: rig.claude_client.requests, timeout=10)
    rig.behaviour["person"] = False
    assert wait_for(lambda: rig.events(source="ai", kind="person_left"), timeout=10)
    attrs = rig.events(source="ai", kind="person_left")[0]["metadata"]["attributes"]
    assert attrs["backpack"]["value"] == "yes" and attrs["backpack"]["source"].startswith("claude:")
    assert attrs["headwear"]["value"] == "unknown"
    assert "age" not in attrs
    assert rig.ai.status()["attributes_cloud"]["succeeded"] >= 1


def test_claude_offline_does_not_affect_detection(rig: Rig):
    rig.claude_client.mode = "error"  # no internet
    rig.enable(min_track_hits=2, attributes=AttributeProviderKind.CLAUDE)
    assert wait_for(lambda: rig.events(source="ai", kind="person_detected"), timeout=8)
    assert wait_for(lambda: rig.ai.status()["attributes_cloud"]["failed"] >= 1, timeout=8)
    assert _mode(rig) == AIMode.ACTIVE


def test_ai_can_be_switched_off_per_camera_without_reconnecting(rig: Rig):
    rig.enable(min_track_hits=2)
    assert wait_for(lambda: rig.events(source="ai", kind="person_detected"), timeout=8)
    handle = rig.manager.stream_handle(rig.camera.id)
    cfg = rig.manager.get_camera(rig.camera.id).config
    data = CameraInput(name=cfg.name, source_type=cfg.source_type, source_uri=cfg.source_uri,
                       options={**cfg.options, "ai_enabled": False})  # fmt: skip
    rig.manager.update_camera(rig.camera.id, data)
    rig.ai._camera_cache = (0.0, [])  # don't wait for the 1 s camera-list cache in the test
    assert wait_for(lambda: any(e["metadata"]["reason"] == "AI disabled for this camera"
                                for e in rig.events(source="ai", kind="person_left")), timeout=8)  # fmt: skip
    assert rig.manager.stream_handle(rig.camera.id) is handle  # stream was NOT restarted
    assert _mode(rig) == AIMode.ACTIVE  # AI stays on for other cameras
    assert rig.ai.status()["cameras_ai_disabled"] == 1
    calls = sum(p.calls for p in FakeAIProvider.instances)
    before = len(rig.events(source="standard"))
    assert wait_for(lambda: len(rig.events(source="standard")) > before, timeout=10)
    assert sum(p.calls for p in FakeAIProvider.instances) == calls
