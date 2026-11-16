"""Optional cloud attribute provider using Claude (Anthropic API).

Disabled unless the operator selects it. Privacy and cost safeguards:
* one request per confirmed person track (never per frame), rate-limited per minute;
* only a downscaled crop of that person is sent — never the full frame, never stored;
* the model is instructed to report ONLY visible clothing/accessory attributes and is
  constrained by a JSON schema that has no field for identity, age, gender, ethnicity, emotion
  or any other personal or sensitive trait;
* the request runs on a background worker with a bounded queue: it never blocks video capture,
  motion detection, person detection or event storage;
* any failure (offline, timeout, rate limit, refusal, bad output) yields ``unknown`` attributes.

The API key comes only from ``ACI_AGENT_AI_API_KEY`` (edge agent .env); it is never logged,
returned by the API or sent anywhere except to the Anthropic API.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from aci_agent.ai_engine.attributes.models import (
    LOWER_TYPES,
    UNKNOWN,
    UPPER_TYPES,
    AppearanceAttributes,
    AttributeValue,
)

logger = logging.getLogger("aci.agent.ai.claude")

MAX_CROP_SIDE = 384  # px — enough for clothing, low detail otherwise
COLOR_VALUES = [
    "black",
    "white",
    "gray",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
    "brown",
    "beige",
    "multicolor",
    "unknown",
]
YES_NO = ["yes", "no", "unknown"]

SYSTEM_PROMPT = (
    "You describe the VISIBLE clothing and carried items of the single person in a security-camera "
    "crop, for an anonymous video analytics system. Report only what is clearly visible: upper and "
    "lower clothing colour and type, and whether a bag, a backpack or headwear is visible. "
    'Use "unknown" whenever the image is unclear, the item is cut off, or you are not confident. '
    "Do not identify the person and do not describe or guess identity, face, age, gender, ethnicity, "
    "body shape, emotion, health, or any personal or sensitive characteristic."
)


def _attr_schema(values: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": "string", "enum": values},
            "confidence": {"type": "number", "description": "0.0–1.0"},
        },
        "required": ["value", "confidence"],
        "additionalProperties": False,
    }


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "upper_color": _attr_schema(COLOR_VALUES),
        "lower_color": _attr_schema(COLOR_VALUES),
        "upper_type": _attr_schema([*UPPER_TYPES, UNKNOWN]),
        "lower_type": _attr_schema([*LOWER_TYPES, UNKNOWN]),
        "bag": _attr_schema(YES_NO),
        "backpack": _attr_schema(YES_NO),
        "headwear": _attr_schema(YES_NO),
    },
    "required": ["upper_color", "lower_color", "upper_type", "lower_type", "bag", "backpack", "headwear"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class AttributeJob:
    camera_id: str
    track_id: str
    crop: np.ndarray


class ClaudeAttributeProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5-5",
        timeout_s: float = 20.0,
        max_calls_per_minute: int = 10,
        client_factory: Callable[[str, float], object] | None = None,
        queue_size: int = 4,
    ) -> None:
        self.model = model
        self.source = f"claude:{model}"
        self.max_calls_per_minute = max_calls_per_minute
        self._client_factory = client_factory or self._default_client
        self._api_key = api_key
        self._timeout = timeout_s
        self._client = None
        self._jobs: queue.Queue[AttributeJob] = queue.Queue(maxsize=queue_size)
        self._calls: deque[float] = deque()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {
            "requests": 0,
            "succeeded": 0,
            "failed": 0,
            "refused": 0,
            "skipped_rate_limit": 0,
            "skipped_queue_full": 0,
            "last_error": None,
        }

    @staticmethod
    def _default_client(api_key: str, timeout: float):
        import anthropic  # optional dependency: `uv sync --extra claude`

        # Retries off: a late answer is useless for live video; failures fall back to "unknown".
        return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def start(self, on_result: Callable[[str, str, AppearanceAttributes], None]) -> None:
        self._on_result = on_result
        self._thread = threading.Thread(target=self._run, name="ai-claude-attributes", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def submit(self, camera_id: str, track_id: str, crop: np.ndarray) -> bool:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > 60:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls_per_minute:
            self.stats["skipped_rate_limit"] += 1
            return False
        try:
            self._jobs.put_nowait(AttributeJob(camera_id, track_id, crop))
        except queue.Full:
            self.stats["skipped_queue_full"] += 1
            return False
        self._calls.append(now)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            attrs = self.analyse(job.crop)
            try:
                self._on_result(job.camera_id, job.track_id, attrs)
            except Exception:  # noqa: BLE001
                logger.exception("Attribute result handler failed")

    # ------------------------------------------------------------------ request

    @staticmethod
    def encode_crop(crop: np.ndarray) -> str:
        h, w = crop.shape[:2]
        scale = min(1.0, MAX_CROP_SIDE / max(h, w))
        if scale < 1.0:
            crop = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("JPEG encoding failed")
        return base64.standard_b64encode(buf.tobytes()).decode("ascii")

    def analyse(self, crop: np.ndarray) -> AppearanceAttributes:
        """Synchronous request. Returns all-unknown attributes on any failure."""
        if not self.available or crop is None or crop.size == 0:
            return AppearanceAttributes()
        self.stats["requests"] += 1
        try:
            if self._client is None:
                self._client = self._client_factory(self._api_key, self._timeout)
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                # Simple visual classification: low effort keeps latency and cost down.
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                # Server-side refusal fallback (opt-in): a declined request is retried on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": self.encode_crop(crop),
                                },
                            },
                            {"type": "text", "text": "Describe this person's visible clothing and carried items."},
                        ],
                    }
                ],
            )
        except Exception as exc:  # network down, timeout, auth, rate limit, missing SDK …
            self.stats["failed"] += 1
            self.stats["last_error"] = type(exc).__name__
            logger.warning("Claude attribute request failed: %s", type(exc).__name__)
            return AppearanceAttributes()

        if getattr(response, "stop_reason", None) == "refusal":
            self.stats["refused"] += 1
            return AppearanceAttributes()
        try:
            text = next(b.text for b in response.content if getattr(b, "type", None) == "text")
            data = json.loads(text)
        except (StopIteration, ValueError):
            self.stats["failed"] += 1
            self.stats["last_error"] = "invalid_output"
            return AppearanceAttributes()

        attrs = AppearanceAttributes()
        for name, raw in data.items():
            if not hasattr(attrs, name) or not isinstance(raw, dict):
                continue  # ignore anything outside the allow-listed attributes
            value = str(raw.get("value", UNKNOWN))
            conf = float(raw.get("confidence", 0) or 0)
            if value in (UNKNOWN, "multicolor") and name.endswith("_color"):
                value = UNKNOWN if value == UNKNOWN else "multicolor"
            setattr(
                attrs,
                name,
                AttributeValue(value, round(max(0.0, min(1.0, conf)), 2) if value != UNKNOWN else 0.0, self.source),
            )
        self.stats["succeeded"] += 1
        return attrs
