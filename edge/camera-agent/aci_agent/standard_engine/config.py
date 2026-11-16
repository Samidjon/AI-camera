"""Per-camera vision settings (validated, bounded, stored as JSON in the local database)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MotionAlgorithm(StrEnum):
    FRAME_DIFF = "frame_diff"  # consecutive-frame differencing: fast, reacts only to change
    BACKGROUND = "background"  # running-average background subtraction: cheap, adapts slowly
    MOG2 = "mog2"  # Gaussian mixture model: robust default, handles gradual light changes
    KNN = "knn"  # K-nearest-neighbours background model: good with dynamic backgrounds


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = True
    algorithm: MotionAlgorithm = MotionAlgorithm.MOG2
    # 1 = least sensitive, 100 = most sensitive. Mapped to a pixel-difference threshold.
    sensitivity: int = Field(default=60, ge=1, le=100)
    # Smallest motion region kept, as a fraction of the frame area (0.002 = 0.2 %).
    min_object_area: float = Field(default=0.002, ge=0.0001, le=0.5)
    # Fragments closer than this (fraction of frame width) are merged into one motion object.
    merge_distance: float = Field(default=0.06, ge=0.0, le=0.3)
    # Noise reduction: Gaussian blur kernel (odd) and morphology iterations (open + close + dilate).
    blur_kernel: int = Field(default=5, ge=1, le=31)
    morphology_iterations: int = Field(default=2, ge=0, le=6)
    # Analysis is done on a downscaled copy; results are mapped back to full resolution.
    process_width: int = Field(default=640, ge=160, le=1920)
    # Frames analysed per second (frame skipping beyond that). Capture FPS is unaffected.
    process_fps: float = Field(default=8.0, ge=0.5, le=30)
    # Restrict detection to "monitor" zones only (otherwise full frame minus "exclude" zones).
    restrict_to_zones: bool = False
    # Motion is "ended" after this long without any motion object (debounce).
    motion_end_delay_s: float = Field(default=2.0, ge=0.2, le=60)
    # A whole-frame change above this fraction is treated as a lighting change, not motion.
    lighting_change_ratio: float = Field(default=0.6, ge=0.2, le=1.0)
    # Tracker: max centroid jump between analysed frames (fraction of frame diagonal), lifetime.
    track_max_distance: float = Field(default=0.15, ge=0.01, le=1.0)
    track_max_missed: int = Field(default=5, ge=1, le=50)
    # Interval of the "camera_motion_activity" summary event.
    activity_interval_s: int = Field(default=60, ge=10, le=3600)

    def threshold(self) -> int:
        """Pixel intensity difference threshold derived from sensitivity (higher sensitivity → lower threshold)."""
        return int(round(60 - (self.sensitivity - 1) * (52 / 99)))  # 60 … 8
