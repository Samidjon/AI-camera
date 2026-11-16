"""AI building blocks: model registry, ByteTrack, local colour attributes, Claude provider, YOLOX."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from aci_agent.ai_engine.attributes.claude_provider import OUTPUT_SCHEMA, ClaudeAttributeProvider
from aci_agent.ai_engine.attributes.local_color import analyse_colors
from aci_agent.ai_engine.attributes.models import ATTRIBUTE_NAMES, UNKNOWN
from aci_agent.ai_engine.models.registry import BUILTIN_MODELS, ModelRegistry, ModelStatus
from aci_agent.ai_engine.providers.base import Detection
from aci_agent.ai_engine.tracking.bytetrack import ByteTracker
from tests.ai_fakes import FakeClaudeClient, fake_registry

MODEL = Path("models/object_detection_yolox_2022nov.onnx")


# ---------------------------------------------------------------- registry


def test_registry_missing_valid_and_corrupted(tmp_path: Path):
    reg, path = fake_registry(tmp_path)
    assert reg.validate("yolox_s_coco_2022nov").status == ModelStatus.VALID
    path.write_bytes(path.read_bytes()[:-1] + b"X")  # same size, one byte flipped
    os.utime(path, None)
    state = reg.validate("yolox_s_coco_2022nov")
    assert state.status == ModelStatus.INVALID and "SHA-256" in state.detail
    path.unlink()
    assert reg.validate("yolox_s_coco_2022nov").status == ModelStatus.MISSING


def test_builtin_models_are_pinned_local_files():
    for spec in BUILTIN_MODELS:
        assert len(spec.sha256) == 64 and spec.size_bytes > 0 and spec.filename.endswith(".onnx")
    reg = ModelRegistry(Path("does-not-exist"))
    assert all(s.status == ModelStatus.MISSING for s in reg.list())  # never downloads by itself


# ---------------------------------------------------------------- ByteTrack


def _det(x, conf=0.9, y=100, w=60, h=160):
    return Detection(float(x), float(y), float(w), float(h), conf, 0, "person")


def test_bytetrack_keeps_id_through_low_confidence_occlusion():
    tr = ByteTracker("camABCDEF", min_hits=3, buffer_frames=10)
    ids = set()
    for i in range(30):
        conf = 0.25 if 10 <= i <= 14 else 0.9  # partially occluded → low score
        ids |= {t.track_id for t in tr.update([_det(40 + i * 8, conf)], i * 0.2).confirmed}
    assert len(ids) == 1 and next(iter(ids)).startswith("p-camABC-")


def test_bytetrack_entry_exit_lifecycle_and_flicker():
    tr = ByteTracker("cam", min_hits=3, buffer_frames=3)
    assert tr.update([_det(100)], 0).entered == []  # tentative, not yet an entry
    tr.update([_det(108)], 0.2)
    upd = tr.update([_det(116)], 0.4)
    assert len(upd.entered) == 1
    exited = []
    for i in range(6):
        exited += tr.update([], 1 + i * 0.2).exited
    assert len(exited) == 1 and exited[0].duration_s >= 0.4
    flick = ByteTracker("cam", min_hits=3)
    flick.update([_det(300)], 0)
    for i in range(5):
        assert flick.update([], 1 + i).exited == []  # single-frame flicker never produces events


def test_bytetrack_ids_not_reused_and_two_people():
    tr = ByteTracker("cam", min_hits=1, buffer_frames=1)
    first = tr.update([_det(50), _det(400)], 0).entered
    assert len({t.track_id for t in first}) == 2
    for i in range(3):
        tr.update([], 1 + i)
    again = tr.update([_det(50)], 5).entered
    assert again and again[0].track_id not in {t.track_id for t in first}


# ---------------------------------------------------------------- local attributes


def _person_image(upper_bgr, lower_bgr):
    img = np.full((400, 300, 3), 128, np.uint8)
    img[100:180, 100:200] = upper_bgr  # 20–50 % of a 50..330 box
    img[180:340, 100:200] = upper_bgr
    img[205:340, 100:200] = lower_bgr
    return img


def test_local_colors_detects_clear_colors():
    img = _person_image((0, 0, 220), (200, 60, 20))  # red top, blue bottom (BGR)
    attrs = analyse_colors(img, (95, 50, 110, 290))
    assert attrs.upper_color.value == "red" and attrs.upper_color.confidence > 0.5
    assert attrs.lower_color.value == "blue"
    assert attrs.upper_color.source == "local_color_v1"
    assert attrs.backpack.value == UNKNOWN and attrs.headwear.value == UNKNOWN  # not guessed


def test_local_colors_unknown_when_unreliable():
    rng = np.random.default_rng(1)
    noisy = rng.integers(0, 255, (400, 300, 3), dtype=np.uint8)  # patterned / mixed
    attrs = analyse_colors(noisy, (50, 30, 150, 330))
    assert attrs.upper_color.value == UNKNOWN
    cut = analyse_colors(_person_image((0, 0, 220), (200, 60, 20)), (95, 200, 110, 200))  # touches frame bottom
    assert cut.upper_color.value == UNKNOWN and cut.lower_color.value == UNKNOWN


def test_head_and_shoulders_view_reports_no_colours():
    """Regression: a close-up of a face must never yield a 'clothing colour' (it would be skin)."""
    img = np.full((480, 640, 3), 200, np.uint8)
    img[100:470, 120:520] = (120, 150, 210)  # skin-like tone (BGR)
    attrs = analyse_colors(img, (110, 90, 420, 380))  # aspect < 1.6
    assert attrs.upper_color.value == UNKNOWN and attrs.lower_color.value == UNKNOWN


def test_skin_tone_pixels_are_excluded_from_the_vote():
    for skin in [(120, 150, 210), (60, 90, 150), (40, 60, 100), (150, 180, 230)]:  # light to dark (BGR)
        img = np.full((400, 300, 3), 128, np.uint8)
        img[50:350, 100:200] = skin
        attrs = analyse_colors(img, (95, 40, 110, 320))
        assert attrs.upper_color.value == UNKNOWN, skin


# ---------------------------------------------------------------- Claude provider (offline, faked client)


def _claude(mode="ok", **kw):
    client = FakeClaudeClient(mode)
    p = ClaudeAttributeProvider("sk-ant-test", client_factory=lambda key, t: client, **kw)
    return p, client


def test_claude_request_is_privacy_constrained():
    p, client = _claude()
    crop = np.full((900, 400, 3), 90, np.uint8)
    attrs = p.analyse(crop)
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"]["schema"] is OUTPUT_SCHEMA
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]
    assert "Do not identify" in req["system"]
    image = req["messages"][0]["content"][0]
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(image["source"]["data"]), np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) <= 384  # downscaled crop, never the full frame
    # schema only allows visible clothing / accessory attributes
    assert set(OUTPUT_SCHEMA["properties"]) == set(ATTRIBUTE_NAMES)
    assert attrs.upper_color.value == "red" and attrs.backpack.value == "yes"
    assert attrs.upper_type.source == "claude:claude-opus-5-5"
    assert not hasattr(attrs, "age")  # a field outside the allow-list is dropped
    assert attrs.headwear.value == UNKNOWN and attrs.headwear.confidence == 0.0


@pytest.mark.parametrize("mode", ["error", "refusal", "garbage"])
def test_claude_failures_yield_unknown(mode):
    p, _ = _claude(mode)
    attrs = p.analyse(np.full((200, 100, 3), 90, np.uint8))
    assert all(getattr(attrs, n).value == UNKNOWN for n in ATTRIBUTE_NAMES)
    assert p.stats["refused" if mode == "refusal" else "failed"] == 1


def test_claude_without_key_never_calls():
    p = ClaudeAttributeProvider("", client_factory=lambda k, t: pytest.fail("must not create a client"))
    assert not p.available
    assert p.analyse(np.zeros((50, 50, 3), np.uint8)).upper_color.value == UNKNOWN


def test_claude_rate_limit_per_minute():
    client = FakeClaudeClient("ok", delay_s=0.2)
    p = ClaudeAttributeProvider("k", client_factory=lambda k, t: client, max_calls_per_minute=3, queue_size=50)
    results = []
    p.start(lambda cam, tid, a: results.append(tid))
    started = time.perf_counter()
    accepted = [p.submit("c", f"t{i}", np.zeros((40, 20, 3), np.uint8)) for i in range(10)]
    assert time.perf_counter() - started < 0.1  # submit never waits for the network
    assert sum(accepted) == 3 and p.stats["skipped_rate_limit"] == 7
    deadline = time.time() + 5
    while len(results) < 3 and time.time() < deadline:
        time.sleep(0.05)
    p.stop()
    assert len(results) == 3 and len(client.requests) == 3


def test_claude_queue_is_bounded():
    p = ClaudeAttributeProvider("k", client_factory=lambda k, t: FakeClaudeClient(), max_calls_per_minute=100,
                                queue_size=2)  # fmt: skip
    # worker not started: nothing drains the queue
    accepted = [p.submit("c", f"t{i}", np.zeros((40, 20, 3), np.uint8)) for i in range(5)]
    assert accepted == [True, True, False, False, False] and p.stats["skipped_queue_full"] == 3


# ---------------------------------------------------------------- real YOLOX model (needs ./models)

needs_model = pytest.mark.skipif(not MODEL.exists(), reason="YOLOX model not present in ./models")


@pytest.mark.model
@needs_model
def test_real_model_loads_validates_and_runs():
    from aci_agent.ai_engine.providers.opencv_yolox import OpenCVYoloxProvider

    reg = ModelRegistry(Path("models"))
    assert reg.validate("yolox_s_coco_2022nov").status == ModelStatus.VALID
    p = OpenCVYoloxProvider()
    p.initialize()
    p.load_model(str(MODEL))
    assert p.health_check().healthy
    assert p.predict(np.zeros((480, 640, 3), np.uint8)) == []  # empty scene → no people


@pytest.mark.model
@needs_model
def test_unavailable_gpu_falls_back_to_cpu():
    from aci_agent.ai_engine.providers.opencv_yolox import OpenCVYoloxProvider

    if cv2.cuda.getCudaEnabledDeviceCount() > 0:
        pytest.skip("this machine has a CUDA GPU")
    p = OpenCVYoloxProvider(backend="cuda")
    p.initialize()
    assert p.backend == "cpu" and any("GPU" in w for w in p.warnings)
    p.load_model(str(MODEL))
    assert p.health_check().healthy


@pytest.mark.model
@needs_model
def test_corrupted_model_file_is_rejected(tmp_path: Path):
    from aci_agent.ai_engine.providers.base import AIProviderError
    from aci_agent.ai_engine.providers.opencv_yolox import OpenCVYoloxProvider

    bad = tmp_path / "broken.onnx"
    bad.write_bytes(MODEL.read_bytes()[:5_000_000])  # truncated download
    p = OpenCVYoloxProvider()
    p.initialize()
    with pytest.raises(AIProviderError):
        p.load_model(str(bad))
