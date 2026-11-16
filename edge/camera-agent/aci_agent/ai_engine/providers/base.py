"""AIProvider: the single interface the rest of the system uses for AI inference.

Swapping the AI implementation (another ONNX model, TensorRT, a different runtime) means writing
a new provider — the engine, tracker, events, API and dashboard stay untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, slots=True)
class Detection:
    x: float  # full-resolution pixels
    y: float
    w: float
    h: float
    confidence: float
    class_id: int
    label: str

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass(slots=True)
class ProviderHealth:
    healthy: bool
    detail: str = ""
    backend: str = "cpu"
    warnings: list[str] = field(default_factory=list)


class AIProviderError(Exception):
    """Inference/model failure inside a provider."""


class AIProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def initialize(self) -> None:
        """Prepare the runtime (choose backend, check environment). Must not load weights."""

    @abstractmethod
    def load_model(self, model_path: str) -> None:
        """Load and warm up a model. Raises AIProviderError on any failure."""

    @abstractmethod
    def predict(self, image: np.ndarray) -> list[Detection]:
        """Run inference on one BGR frame. Must be called from a single thread at a time."""

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """Cheap self-test (e.g. inference on a blank frame)."""

    @abstractmethod
    def shutdown(self) -> None:
        """Release model and runtime resources."""
