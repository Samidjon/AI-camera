"""Model registry: which models exist, their versions, files, checksums and status.

Models are LOCAL files only. The agent never downloads a model by itself: an operator places the
file in ``ACI_AGENT_AI_MODELS_DIR`` (see docs for the verified source) and the registry validates
it against the pinned SHA-256 before it can be loaded.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class ModelStatus(StrEnum):
    MISSING = "missing"  # file not present
    UNVERIFIED = "unverified"  # present, not yet checked
    INVALID = "invalid"  # checksum mismatch / corrupted / unparseable
    VALID = "valid"  # checksum ok
    LOADED = "loaded"  # currently in use


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    name: str
    version: str
    filename: str
    sha256: str
    size_bytes: int
    task: str
    classes: str
    input_size: int
    license: str
    source: str
    provider: str = "opencv-dnn-yolox"


BUILTIN_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="yolox_s_coco_2022nov",
        name="YOLOX-S (COCO)",
        version="2022nov",
        filename="object_detection_yolox_2022nov.onnx",
        sha256="c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
        size_bytes=35_858_002,
        task="object detection (person class used)",
        classes="COCO-80",
        input_size=640,
        license="Apache-2.0",
        source="https://huggingface.co/opencv/object_detection_yolox (OpenCV Model Zoo)",
    ),
    ModelSpec(
        id="yolox_s_coco_2022nov_int8",
        name="YOLOX-S (COCO, int8 quantized)",
        version="2022nov-int8",
        filename="object_detection_yolox_2022nov_int8.onnx",
        sha256="01a3b0f400b30bc1e45230e991b2e499ab42622485a330021947333fbaf03935",
        size_bytes=9_079_452,
        task="object detection (person class used)",
        classes="COCO-80",
        input_size=640,
        license="Apache-2.0",
        source="https://huggingface.co/opencv/object_detection_yolox (OpenCV Model Zoo)",
    ),
)


@dataclass(slots=True)
class ModelState:
    spec: ModelSpec
    path: Path
    status: ModelStatus = ModelStatus.UNVERIFIED
    detail: str = ""
    checked_at: datetime | None = None
    _hash_key: tuple | None = field(default=None, repr=False)  # (mtime, size) of the last hash check


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


class ModelRegistry:
    def __init__(self, models_dir: Path, specs: tuple[ModelSpec, ...] = BUILTIN_MODELS) -> None:
        self.models_dir = Path(models_dir)
        self._lock = threading.Lock()
        self._states = {s.id: ModelState(s, self.models_dir / s.filename) for s in specs}

    def get(self, model_id: str) -> ModelState | None:
        return self._states.get(model_id)

    def list(self) -> list[ModelState]:
        for state in self._states.values():
            if not state.path.exists():
                state.status, state.detail = ModelStatus.MISSING, f"Place {state.spec.filename} in {self.models_dir}"
            elif state.status == ModelStatus.MISSING:
                state.status, state.detail = ModelStatus.UNVERIFIED, ""
        return list(self._states.values())

    def validate(self, model_id: str) -> ModelState:
        """Check presence and SHA-256 (cached by mtime+size, so repeated loads are cheap)."""
        with self._lock:
            state = self._states.get(model_id)
            if state is None:
                raise KeyError(model_id)
            now = datetime.now(UTC)
            if not state.path.is_file():
                state.status, state.detail, state.checked_at = (
                    ModelStatus.MISSING,
                    f"Model file not found: place {state.spec.filename} in {self.models_dir}",
                    now,
                )
                return state
            st = state.path.stat()
            key = (st.st_mtime_ns, st.st_size)
            if state._hash_key == key and state.status in (ModelStatus.VALID, ModelStatus.LOADED):
                return state
            if st.st_size != state.spec.size_bytes:
                state.status = ModelStatus.INVALID
                state.detail = f"Size {st.st_size} B ≠ expected {state.spec.size_bytes} B (corrupted or wrong file)"
            elif sha256_file(state.path) != state.spec.sha256:
                state.status, state.detail = (
                    ModelStatus.INVALID,
                    "SHA-256 mismatch — file corrupted or not the verified model",
                )
            else:
                state.status, state.detail = ModelStatus.VALID, "Checksum verified"
            state._hash_key = key
            state.checked_at = now
            return state

    def mark(self, model_id: str, status: ModelStatus, detail: str = "") -> None:
        with self._lock:
            state = self._states[model_id]
            state.status, state.detail = status, detail or state.detail
            if status == ModelStatus.INVALID:
                state._hash_key = None
