"""YOLOX (COCO-pretrained) person detector running on OpenCV DNN.

No PyTorch / ONNX Runtime needed: OpenCV (already a core dependency) executes the ONNX graph on
CPU, or on CUDA when OpenCV was built with it. Model: OpenCV Model Zoo ``object_detection_yolox``.
Pre-/post-processing follows the YOLOX reference: letterbox to 640×640 (pad 114), raw float input,
grid/stride decoding, objectness × class score, NMS.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from aci_agent.ai_engine.providers.base import AIProvider, AIProviderError, Detection, ProviderHealth

logger = logging.getLogger("aci.agent.ai.yolox")

PERSON_CLASS_ID = 0  # COCO
STRIDES = (8, 16, 32)


class OpenCVYoloxProvider(AIProvider):
    name = "opencv-dnn-yolox"

    def __init__(
        self,
        input_size: int = 640,
        confidence_threshold: float = 0.45,
        nms_threshold: float = 0.5,
        classes: tuple[int, ...] = (PERSON_CLASS_ID,),
        backend: str = "cpu",
    ) -> None:
        self.input_size = input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.classes = classes
        self.requested_backend = backend
        self.backend = "cpu"
        self.warnings: list[str] = []
        self._net = None
        self._grids, self._strides = self._make_grids(input_size)

    @staticmethod
    def _make_grids(size: int) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for s in STRIDES:
            n = size // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((1, grid.shape[1], 1), s))
        return np.concatenate(grids, 1).astype(np.float32), np.concatenate(strides, 1).astype(np.float32)

    # ------------------------------------------------------------------ AIProvider

    def initialize(self) -> None:
        self.warnings.clear()
        if self.requested_backend == "cuda":
            count = cv2.cuda.getCudaEnabledDeviceCount() if hasattr(cv2, "cuda") else 0
            if count > 0:
                self.backend = "cuda"
            else:
                self.backend = "cpu"
                self.warnings.append("GPU requested but not available to OpenCV — running on CPU")
        else:
            self.backend = "cpu"

    def load_model(self, model_path: str) -> None:
        try:
            net = cv2.dnn.readNetFromONNX(model_path)
        except cv2.error as exc:
            raise AIProviderError(f"Model file can't be parsed: {str(exc).splitlines()[-1][:200]}") from exc
        if net.empty():
            raise AIProviderError("Model loaded empty")
        if self.backend == "cuda":
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
        self._net = net
        # Warm-up + shape validation: catches wrong/corrupted graphs before the first real frame.
        out = self._forward(np.zeros((self.input_size, self.input_size, 3), np.uint8))
        if out.ndim != 3 or out.shape[2] != 85:
            self._net = None
            raise AIProviderError(f"Unexpected model output shape {tuple(out.shape)} (expected N×85 YOLOX/COCO)")

    def _forward(self, padded: np.ndarray) -> np.ndarray:
        if self._net is None:
            raise AIProviderError("Model not loaded")
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[np.newaxis].astype(np.float32))
        self._net.setInput(blob)
        try:
            return self._net.forward()
        except cv2.error as exc:
            raise AIProviderError(f"Inference failed: {str(exc).splitlines()[-1][:200]}") from exc

    def predict(self, image: np.ndarray) -> list[Detection]:
        if image is None or image.size == 0:
            return []
        h, w = image.shape[:2]
        ratio = min(self.input_size / h, self.input_size / w)
        resized = cv2.resize(image, (int(w * ratio), int(h * ratio)), interpolation=cv2.INTER_LINEAR)
        padded = np.full((self.input_size, self.input_size, 3), 114, np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized

        out = self._forward(padded)[0]
        out[:, :2] = (out[:, :2] + self._grids[0]) * self._strides[0]
        out[:, 2:4] = np.exp(out[:, 2:4]) * self._strides[0]
        scores_all = out[:, 4:5] * out[:, 5:]
        class_ids = scores_all.argmax(1)
        scores = scores_all[np.arange(len(class_ids)), class_ids]
        keep = (scores >= self.confidence_threshold) & np.isin(class_ids, self.classes)
        if not keep.any():
            return []
        boxes, scores, class_ids = out[keep, :4], scores[keep], class_ids[keep]
        xywh = np.stack(
            [
                (boxes[:, 0] - boxes[:, 2] / 2) / ratio,
                (boxes[:, 1] - boxes[:, 3] / 2) / ratio,
                boxes[:, 2] / ratio,
                boxes[:, 3] / ratio,
            ],
            1,
        )
        idx = cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), self.confidence_threshold, self.nms_threshold)
        dets = []
        for i in np.array(idx).flatten():
            x, y, bw, bh = xywh[i]
            x0, y0 = max(0.0, float(x)), max(0.0, float(y))
            x1, y1 = min(float(w), float(x + bw)), min(float(h), float(y + bh))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            dets.append(Detection(x0, y0, x1 - x0, y1 - y0, float(scores[i]), int(class_ids[i]), "person"))
        return dets

    def health_check(self) -> ProviderHealth:
        if self._net is None:
            return ProviderHealth(False, "Model not loaded", self.backend, list(self.warnings))
        try:
            self.predict(np.zeros((64, 64, 3), np.uint8))
        except AIProviderError as exc:
            return ProviderHealth(False, str(exc), self.backend, list(self.warnings))
        return ProviderHealth(True, "ok", self.backend, list(self.warnings))

    def shutdown(self) -> None:
        self._net = None
