"""Motion detection with OpenCV (no neural networks).

Pipeline per frame: downscale → grayscale → Gaussian blur (noise reduction) → foreground mask
(algorithm-specific) → threshold → zone mask → morphology (open: remove speckles, close + dilate:
merge fragments) → contours → regions filtered by minimum area → mapped to full resolution.

A sudden whole-frame change (lights switched on/off, camera auto-exposure, IR mode switch) is
recognised as a *lighting change*: the background model is reset and no motion is reported for
that frame, instead of flooding the system with false motion.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import cv2
import numpy as np

from aci_agent.standard_engine.config import MotionAlgorithm, VisionConfig
from aci_agent.standard_engine.geometry import Box, Point


@dataclass(slots=True)
class MotionRegion:
    """A connected moving area. It is a motion object, not a person or a vehicle."""

    box: Box  # full-resolution pixels
    area_px: float  # contour area, full-resolution pixels


@dataclass(slots=True)
class MotionResult:
    regions: list[MotionRegion] = field(default_factory=list)
    motion_ratio: float = 0.0  # foreground pixels / analysed pixels
    lighting_change: bool = False
    warming_up: bool = False  # background model still learning
    mask: np.ndarray | None = None  # downscaled binary mask (for the "motion regions" overlay)
    scale: float = 1.0  # full-res pixels per mask pixel


class MotionDetector(ABC):
    """Common preprocessing and post-processing; subclasses provide the foreground mask."""

    warmup_frames = 0

    def __init__(self, config: VisionConfig) -> None:
        self.config = config
        self._frames = 0
        self._zone_mask: np.ndarray | None = None
        self._zone_key: tuple | None = None

    @abstractmethod
    def _foreground(self, gray: np.ndarray) -> np.ndarray:
        """Return a uint8 foreground mask (0/255) for a blurred grayscale frame."""

    def reset(self) -> None:
        self._frames = 0

    def detect(
        self,
        frame: np.ndarray | None,
        include: list[list[Point]] | None = None,
        exclude: list[list[Point]] | None = None,
    ) -> MotionResult:
        if frame is None or frame.size == 0 or frame.ndim < 2:
            return MotionResult()  # empty frame: nothing to analyse, state untouched
        cfg = self.config
        h, w = frame.shape[:2]
        scale = w / cfg.process_width if w > cfg.process_width else 1.0
        small = (
            cv2.resize(frame, (int(w / scale), int(h / scale)), interpolation=cv2.INTER_AREA) if scale > 1 else frame
        )
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        k = cfg.blur_kernel if cfg.blur_kernel % 2 == 1 else cfg.blur_kernel + 1
        if k > 1:
            gray = cv2.GaussianBlur(gray, (k, k), 0)

        fg = self._foreground(gray)
        self._frames += 1
        if self._frames <= self.warmup_frames:
            return MotionResult(warming_up=True, scale=scale)

        _, fg = cv2.threshold(fg, 127, 255, cv2.THRESH_BINARY)
        roi = self._roi_mask(gray.shape, include, exclude)
        if roi is not None:
            fg = cv2.bitwise_and(fg, roi)
            analysed = max(int(cv2.countNonZero(roi)), 1)
        else:
            analysed = fg.size
        ratio = cv2.countNonZero(fg) / analysed
        if ratio >= cfg.lighting_change_ratio:
            self.reset()  # rebuild the background under the new lighting
            return MotionResult(motion_ratio=ratio, lighting_change=True, scale=scale)

        if cfg.morphology_iterations:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel, iterations=1)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel, iterations=cfg.morphology_iterations)
            fg = cv2.dilate(fg, kernel, iterations=cfg.morphology_iterations)

        min_area = cfg.min_object_area * gray.shape[0] * gray.shape[1]
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            regions.append(MotionRegion(Box(x * scale, y * scale, bw * scale, bh * scale), area * scale * scale))
        regions = merge_close_regions(regions, gap=cfg.merge_distance * w)
        regions.sort(key=lambda r: r.area_px, reverse=True)
        return MotionResult(regions=regions[:64], motion_ratio=ratio, mask=fg, scale=scale)

    def _roi_mask(self, shape: tuple[int, int], include, exclude) -> np.ndarray | None:
        key = (shape, _freeze(include), _freeze(exclude))
        if key == self._zone_key:
            return self._zone_mask
        mask = None
        if include or exclude:
            h, w = shape
            mask = np.zeros((h, w), np.uint8) if include else np.full((h, w), 255, np.uint8)
            for poly in include or []:
                cv2.fillPoly(mask, [_to_px(poly, w, h)], 255)
            for poly in exclude or []:
                cv2.fillPoly(mask, [_to_px(poly, w, h)], 0)
        self._zone_mask, self._zone_key = mask, key
        return mask


def merge_close_regions(regions: list[MotionRegion], gap: float) -> list[MotionRegion]:
    """Merge fragments of one moving thing (e.g. leading/trailing edges from frame differencing)
    whose boxes overlap or are closer than ``gap`` pixels."""
    merged = list(regions)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                a, b = merged[i].box, merged[j].box
                if (
                    a.x - gap <= b.x + b.w
                    and b.x - gap <= a.x + a.w
                    and a.y - gap <= b.y + b.h
                    and b.y - gap <= a.y + a.h
                ):
                    x0, y0 = min(a.x, b.x), min(a.y, b.y)
                    x1, y1 = max(a.x + a.w, b.x + b.w), max(a.y + a.h, b.y + b.h)
                    merged[i] = MotionRegion(Box(x0, y0, x1 - x0, y1 - y0), merged[i].area_px + merged[j].area_px)
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _freeze(polys) -> tuple | None:
    return tuple(tuple(map(tuple, p)) for p in polys) if polys else None


def _to_px(poly: list[Point], w: int, h: int) -> np.ndarray:
    return np.array([[int(round(x * (w - 1))), int(round(y * (h - 1)))] for x, y in poly], np.int32)


class FrameDifferenceDetector(MotionDetector):
    warmup_frames = 1

    def __init__(self, config: VisionConfig) -> None:
        super().__init__(config)
        self._prev: np.ndarray | None = None

    def reset(self) -> None:
        super().reset()
        self._prev = None

    def _foreground(self, gray: np.ndarray) -> np.ndarray:
        if self._prev is None or self._prev.shape != gray.shape:
            self._prev = gray
            return np.zeros_like(gray)
        diff = cv2.absdiff(gray, self._prev)
        self._prev = gray
        return np.where(diff >= self.config.threshold(), 255, 0).astype(np.uint8)


class RunningAverageDetector(MotionDetector):
    """Classic background subtraction against an exponentially-weighted running average."""

    warmup_frames = 3
    learning_rate = 0.05

    def __init__(self, config: VisionConfig) -> None:
        super().__init__(config)
        self._bg: np.ndarray | None = None

    def reset(self) -> None:
        super().reset()
        self._bg = None

    def _foreground(self, gray: np.ndarray) -> np.ndarray:
        if self._bg is None or self._bg.shape != gray.shape:
            self._bg = gray.astype(np.float32)
            return np.zeros_like(gray)
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(self._bg))
        cv2.accumulateWeighted(gray, self._bg, self.learning_rate)
        return np.where(diff >= self.config.threshold(), 255, 0).astype(np.uint8)


class _OpenCVSubtractor(MotionDetector):
    warmup_frames = 10

    def __init__(self, config: VisionConfig) -> None:
        super().__init__(config)
        self._model = self._create()

    def _create(self):  # pragma: no cover — overridden
        raise NotImplementedError

    def reset(self) -> None:
        super().reset()
        self._model = self._create()

    def _foreground(self, gray: np.ndarray) -> np.ndarray:
        mask = self._model.apply(gray)
        mask[mask == 127] = 0  # shadows (value 127) are not motion
        return mask


class MOG2Detector(_OpenCVSubtractor):
    def _create(self):
        var = max(4.0, float(self.config.threshold()) ** 2 / 4)
        return cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=var, detectShadows=True)


class KNNDetector(_OpenCVSubtractor):
    def _create(self):
        dist = max(50.0, float(self.config.threshold()) ** 2)
        return cv2.createBackgroundSubtractorKNN(history=300, dist2Threshold=dist, detectShadows=True)


_DETECTORS: dict[MotionAlgorithm, type[MotionDetector]] = {
    MotionAlgorithm.FRAME_DIFF: FrameDifferenceDetector,
    MotionAlgorithm.BACKGROUND: RunningAverageDetector,
    MotionAlgorithm.MOG2: MOG2Detector,
    MotionAlgorithm.KNN: KNNDetector,
}


def create_detector(config: VisionConfig) -> MotionDetector:
    return _DETECTORS[config.algorithm](config)
