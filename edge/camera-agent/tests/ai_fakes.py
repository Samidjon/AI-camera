"""Controllable AI provider / Claude client fakes for AI failure-mode tests."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from aci_agent.ai_engine.models.registry import ModelRegistry, ModelSpec
from aci_agent.ai_engine.providers.base import AIProvider, AIProviderError, Detection, ProviderHealth


class FakeAIProvider(AIProvider):
    """Returns one person walking left→right; behaviour can be changed at runtime."""

    name = "fake-provider"
    instances: list[FakeAIProvider] = []

    def __init__(self, behaviour: dict) -> None:
        self.b = behaviour  # shared dict: tests flip keys while the engine runs
        self.backend = "cpu"
        self.calls = 0
        self.loaded = False
        FakeAIProvider.instances.append(self)

    def initialize(self) -> None:
        self.warnings = list(self.b.get("warnings", []))

    def load_model(self, model_path: str) -> None:
        if self.b.get("fail_load"):
            raise AIProviderError("Model file can't be parsed: corrupted graph")
        self.loaded = True

    def predict(self, image):
        self.calls += 1
        if self.b.get("raise_predict", 0) > 0:
            self.b["raise_predict"] -= 1
            raise AIProviderError("CUDA error: device lost")
        if self.b.get("delay_s"):
            time.sleep(self.b["delay_s"])
        if not self.b.get("person", True):
            return []
        h, w = image.shape[:2]
        x = (self.calls * 25) % max(1, w - 80)
        return [Detection(float(x), h * 0.2, 60.0, h * 0.6, 0.9, 0, "person")]

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(self.loaded, "ok" if self.loaded else "not loaded", "cpu", list(self.warnings))

    def shutdown(self) -> None:
        self.loaded = False


def fake_registry(tmp_path: Path) -> tuple[ModelRegistry, Path]:
    data = b"fake-onnx-model" * 100
    path = tmp_path / "models" / "fake.onnx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    spec = ModelSpec(
        id="yolox_s_coco_2022nov", name="Fake YOLOX", version="test", filename="fake.onnx",
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), task="test", classes="COCO-80",
        input_size=640, license="test", source="test",
    )  # fmt: skip
    return ModelRegistry(path.parent, (spec,)), path


class FakeClaudeClient:
    """Mimics ``anthropic.Anthropic().beta.messages.create`` for offline tests."""

    def __init__(self, mode: str = "ok", delay_s: float = 0.0) -> None:
        self.mode = mode
        self.delay_s = delay_s
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        with self.lock:
            self.requests.append(kwargs)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.mode == "error":
            raise ConnectionError("network unreachable")
        if self.mode == "refusal":
            return SimpleNamespace(stop_reason="refusal", content=[])
        if self.mode == "garbage":
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="not json")])
        payload = (
            '{"upper_color":{"value":"red","confidence":0.9},"lower_color":{"value":"blue","confidence":0.8},'
            '"upper_type":{"value":"jacket","confidence":0.7},"lower_type":{"value":"jeans","confidence":0.6},'
            '"bag":{"value":"no","confidence":0.8},"backpack":{"value":"yes","confidence":0.85},'
            '"headwear":{"value":"unknown","confidence":0.2},"age":{"value":"30","confidence":0.9}}'
        )
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=payload)])
