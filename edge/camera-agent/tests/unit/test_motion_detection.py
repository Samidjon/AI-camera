"""Motion detection on synthetic frames with known ground truth, for every algorithm."""

import numpy as np
import pytest

from aci_agent.standard_engine.config import MotionAlgorithm, VisionConfig
from aci_agent.standard_engine.motion import create_detector
from tests.vision_scenes import H, W, background, moving_box_scene, static_scene, two_boxes_scene

ALGORITHMS = list(MotionAlgorithm)


def _run(alg, scene, frames=60, **cfg):
    det = create_detector(VisionConfig(algorithm=alg, **cfg))
    return [det.detect(scene(i)) for i in range(frames)]


@pytest.mark.parametrize("alg", ALGORITHMS)
def test_single_moving_object(alg):
    results = _run(alg, moving_box_scene(speed=6), frames=60)
    detected = [r for r in results[20:] if r.regions]
    assert len(detected) >= 35  # moving box found in (almost) every analysed frame
    largest = detected[-1].regions[0].box
    assert 30 <= largest.w <= 200 and 50 <= largest.h <= 160  # roughly the 50x80 box (+ motion blur)


@pytest.mark.parametrize("alg", ALGORITHMS)
def test_multiple_moving_objects(alg):
    results = _run(alg, two_boxes_scene(), frames=50)
    counts = [len(r.regions) for r in results[25:45]]
    assert max(counts) >= 2
    assert sum(1 for c in counts if c >= 2) >= 12


@pytest.mark.parametrize("alg", ALGORITHMS)
def test_sensor_noise_is_not_motion(alg):
    results = _run(alg, static_scene(noise=12), frames=60)
    assert sum(len(r.regions) for r in results[15:]) == 0


@pytest.mark.parametrize("alg", ALGORITHMS)
def test_lighting_change_is_not_reported_as_motion(alg):
    det = create_detector(VisionConfig(algorithm=alg))
    for i in range(20):
        det.detect(background(seed=i))
    result = det.detect(background(brightness=200, seed=99))  # lights switched on
    assert result.lighting_change and result.regions == []
    # after re-learning, the brighter static scene produces no motion
    later = [det.detect(background(brightness=200, seed=100 + i)) for i in range(25)]
    assert sum(len(r.regions) for r in later[15:]) == 0


@pytest.mark.parametrize("frame", [None, np.zeros((0, 0, 3), np.uint8), np.array([], np.uint8)])
def test_empty_frame_is_ignored(frame):
    det = create_detector(VisionConfig())
    result = det.detect(frame)
    assert result.regions == [] and not result.lighting_change


def test_exclude_zone_masks_motion():
    # box moves through the left half only; exclude the left half
    left = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]
    scene = moving_box_scene(speed=4, start_x=20)
    plain = create_detector(VisionConfig())
    masked = create_detector(VisionConfig())
    p = [plain.detect(scene(i)) for i in range(50)]
    m = [masked.detect(scene(i), exclude=[left]) for i in range(50)]
    assert sum(len(r.regions) for r in p[20:]) > 0
    assert sum(len(r.regions) for r in m[20:]) == 0


def test_restrict_to_include_zone():
    right = [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)]
    det = create_detector(VisionConfig())
    scene = moving_box_scene(speed=4, start_x=20)  # stays in the left half for these frames
    res = [det.detect(scene(i), include=[right]) for i in range(50)]
    assert sum(len(r.regions) for r in res[20:]) == 0


def test_min_object_area_filters_small_motion():
    small = moving_box_scene(size=(6, 6))
    res = _run(MotionAlgorithm.MOG2, small, frames=50, min_object_area=0.01, morphology_iterations=0)
    assert sum(len(r.regions) for r in res[20:]) == 0


def test_sensitivity_changes_threshold():
    assert VisionConfig(sensitivity=100).threshold() < VisionConfig(sensitivity=1).threshold()


def test_regions_are_mapped_to_full_resolution():
    big = moving_box_scene(speed=6)

    def upscale(i):
        import cv2

        return cv2.resize(big(i), (W * 2, H * 2), interpolation=cv2.INTER_NEAREST)

    res = _run(MotionAlgorithm.MOG2, upscale, frames=40, process_width=640)
    box = next(r for r in reversed(res) if r.regions).regions[0].box
    assert box.h > 100  # full-res coordinates (box is 160 px tall at 2x), not the 640-wide working copy
