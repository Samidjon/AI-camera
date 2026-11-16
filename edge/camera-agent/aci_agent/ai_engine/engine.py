"""AI Engine supervisor: optional person detection + tracking + attributes on top of the
Standard Vision Engine.

Modes: disabled · loading · active · degraded · offline · error · recovering

Failure protection (spec MVP 04 §7): on any AI failure the engine (1) records the error, (2) drops
the faulty inference worker, (3) leaves processing to the Standard Engine — which never stopped —
(4)/(5) capture and event storage are untouched, (6) retries recovery with exponential backoff.
Inference runs on its own thread; a watchdog abandons it if a call exceeds the timeout, so a hung
model can never block video capture, motion detection or local storage.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import numpy as np
import psutil

from aci_agent.ai_engine.attributes.claude_provider import ClaudeAttributeProvider
from aci_agent.ai_engine.attributes.local_color import analyse_colors
from aci_agent.ai_engine.attributes.models import AppearanceAttributes
from aci_agent.ai_engine.configuration import AIEnvSettings, AIRuntimeConfig, AttributeProviderKind
from aci_agent.ai_engine.models.registry import ModelRegistry, ModelStatus
from aci_agent.ai_engine.monitoring.metrics import InferenceMetrics, gpu_status
from aci_agent.ai_engine.providers.base import AIProvider, AIProviderError, Detection
from aci_agent.ai_engine.providers.opencv_yolox import OpenCVYoloxProvider
from aci_agent.ai_engine.tracking.bytetrack import ByteTracker, PersonTrack
from aci_agent.events.models import Event
from aci_agent.events.store import EventStore
from aci_agent.standard_engine.geometry import Box, compass
from aci_agent.standard_engine.rules import LineCrossingCounter
from aci_agent.storage.ai_repository import AIRepository

logger = logging.getLogger("aci.agent.ai")


class AIMode(StrEnum):
    DISABLED = "disabled"
    LOADING = "loading"
    ACTIVE = "active"
    DEGRADED = "degraded"  # running with limitations (CPU fallback, attribute provider unavailable, …)
    OFFLINE = "offline"  # can't run: model file missing / not verified
    ERROR = "error"  # failed; recovery scheduled
    RECOVERING = "recovering"


RUNNING = (AIMode.ACTIVE, AIMode.DEGRADED)
STREAM_STALE_S = 3.0


@dataclass(slots=True)
class _TrackView:
    track_id: str
    box: Box


@dataclass(slots=True)
class CameraAIState:
    tracker: ByteTracker
    lines: LineCrossingCounter = field(default_factory=lambda: LineCrossingCounter(cooldown_s=3.0))
    handle: object | None = None
    queue: object | None = None
    next_due: float = 0.0
    frame_size: tuple[int, int] | None = None
    people: list[dict] = field(default_factory=list)  # for overlays / API
    attributes: dict[str, AppearanceAttributes] = field(default_factory=dict)
    people_seen: int = 0
    last_frame_at: float | None = None


ProviderFactory = Callable[[AIEnvSettings, AIRuntimeConfig], AIProvider]


def default_provider_factory(env: AIEnvSettings, cfg: AIRuntimeConfig) -> AIProvider:
    return OpenCVYoloxProvider(
        confidence_threshold=min(cfg.confidence_threshold, 0.1),  # tracker needs low-conf boxes (ByteTrack)
        nms_threshold=cfg.nms_threshold,
        backend=env.backend,
    )


class AIEngine:
    def __init__(
        self,
        camera_manager,
        store: EventStore,
        repo: AIRepository,
        vision_repo,
        env: AIEnvSettings | None = None,
        registry: ModelRegistry | None = None,
        provider_factory: ProviderFactory = default_provider_factory,
        memory_available_mb: Callable[[], float] | None = None,
        rss_mb: Callable[[], float] | None = None,
        claude_factory: Callable[..., ClaudeAttributeProvider] | None = None,
    ) -> None:
        self.env = env or AIEnvSettings()
        self._cameras = camera_manager
        self._store = store
        self._repo = repo
        self._vision_repo = vision_repo
        self.registry = registry or ModelRegistry(self.env.models_dir)
        self._provider_factory = provider_factory
        self._memory_available_mb = memory_available_mb or (lambda: psutil.virtual_memory().available / 1_048_576)
        self._rss_mb = rss_mb or (lambda: psutil.Process().memory_info().rss / 1_048_576)
        self._claude_factory = claude_factory or ClaudeAttributeProvider
        self.config = repo.get_config()
        self.metrics = InferenceMetrics()
        self._lock = threading.RLock()
        self.mode = AIMode.DISABLED
        self.mode_reason = "AI is disabled"
        self.mode_since = datetime.now(UTC)
        self.warnings: list[str] = []
        self.errors: deque[dict] = deque(maxlen=50)
        self._provider: AIProvider | None = None
        self._generation = 0
        self._inflight: tuple[float, int] | None = None  # (start, generation)
        self._recover_at: float | None = None
        self._attempts = 0
        self._offline_check_at = 0.0
        self._cams: dict[str, CameraAIState] = {}
        self._camera_cache: tuple[float, list] = (0.0, [])
        self._attr_provider: ClaudeAttributeProvider | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._supervisor = threading.Thread(target=self._supervise, name="ai-supervisor", daemon=True)

    # ------------------------------------------------------------------ public control

    def start(self) -> None:
        self._configure_attribute_provider()
        self._supervisor.start()
        self._wake.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._supervisor.is_alive():
            self._supervisor.join(timeout)
        self._stop_inference(reason="AI engine stopped")
        if self._attr_provider is not None:
            self._attr_provider.stop()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.config.enabled = enabled
            self._repo.save_config(self.config)
            self._attempts = 0
            self._recover_at = None
        self._wake.set()

    def update_config(self, new: AIRuntimeConfig) -> AIRuntimeConfig:
        with self._lock:
            reload_model = (new.model_id, new.nms_threshold) != (self.config.model_id, self.config.nms_threshold)
            self.config = self._repo.save_config(new)
            if reload_model and self.mode in RUNNING:
                self._stop_inference(reason="model changed")
                self._set_mode(AIMode.LOADING, "Reloading model")
            for st in self._cams.values():
                st.tracker.high = new.track_high_threshold
                st.tracker.buffer = new.track_buffer_frames
                st.tracker.min_hits = new.min_track_hits
        self._configure_attribute_provider()
        self._wake.set()
        return self.config

    # ------------------------------------------------------------------ state

    def _set_mode(self, mode: AIMode, reason: str) -> None:
        with self._lock:
            if mode == self.mode and reason == self.mode_reason:
                return
            self.mode, self.mode_reason, self.mode_since = mode, reason, datetime.now(UTC)
        log = logger.error if mode == AIMode.ERROR else logger.info
        log("AI mode → %s: %s", mode.value, reason)
        try:
            self._repo.record_status(mode.value, reason)
        except Exception:  # noqa: BLE001
            logger.warning("Could not record AI status history")

    def _record_error(self, kind: str, detail: str) -> None:
        self.errors.appendleft({"at": datetime.now(UTC).isoformat(), "kind": kind, "detail": detail[:300]})

    def status(self) -> dict:
        model = self.registry.get(self.config.model_id)
        return {
            "mode": self.mode.value,
            "reason": self.mode_reason,
            "since": self.mode_since.isoformat(),
            "enabled": self.config.enabled,
            "fallback": "standard_engine" if self.mode not in RUNNING else None,
            "warnings": list(self.warnings),
            "model": {
                "id": self.config.model_id,
                "name": model.spec.name if model else None,
                "version": model.spec.version if model else None,
                "status": model.status.value if model else "unknown",
                "provider": getattr(self._provider, "name", None),
                "backend": getattr(self._provider, "backend", None),
            },
            "metrics": self.metrics.snapshot(),
            "people_now": sum(len(s.people) for s in self._cams.values()),
            "cameras_ai_disabled": sum(1 for c in self._cameras_list() if not c.options.get("ai_enabled", True)),
            "people_seen_session": sum(s.people_seen for s in self._cams.values()),
            "recovery": {
                "attempts": self._attempts,
                "next_attempt_in_s": round(max(0.0, self._recover_at - time.monotonic()), 1)
                if self._recover_at
                else None,
            },
            "attributes_provider": self.config.attributes.value,
            "attributes_cloud": dict(self._attr_provider.stats) if self._attr_provider else None,
            "resources": {
                "process_rss_mb": round(self._rss_mb(), 1),
                "max_rss_mb": self.env.max_rss_mb,
                "system_available_mb": round(self._memory_available_mb(), 0),
                "gpu": gpu_status(),
            },
        }

    def camera_state(self, camera_id: str) -> dict | None:
        st = self._cams.get(camera_id)
        if st is None:
            return None
        return {"people": list(st.people), "frame_size": st.frame_size}

    # ------------------------------------------------------------------ supervisor

    def _supervise(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            try:
                self._supervise_once()
            except Exception as exc:  # noqa: BLE001 — the supervisor must survive anything
                logger.exception("AI supervisor error")
                self._record_error("supervisor", repr(exc))

    def _supervise_once(self) -> None:
        now = time.monotonic()
        if not self.config.enabled:
            if self.mode != AIMode.DISABLED:
                self._stop_inference(reason="AI disabled")
                self._set_mode(AIMode.DISABLED, "AI is disabled")
            return

        # Watchdog: an inference call that exceeds the timeout is abandoned.
        inflight = self._inflight
        if inflight and inflight[1] == self._generation and now - inflight[0] > self.env.inference_timeout_s:
            self.metrics.timeouts += 1
            self._fail("timeout", f"Inference exceeded {self.env.inference_timeout_s:.1f}s")
            return

        if self.mode in RUNNING:
            rss = self._rss_mb()
            if rss > self.env.max_rss_mb:
                self._fail("memory", f"Agent memory {rss:.0f} MB above AI limit {self.env.max_rss_mb:.0f} MB")
            return

        if self.mode == AIMode.ERROR:
            if self._recover_at is not None and now >= self._recover_at:
                self._set_mode(AIMode.RECOVERING, f"Recovery attempt {self._attempts}")
                self._load()
            return

        if self.mode == AIMode.OFFLINE and now < self._offline_check_at:
            return
        if self.mode in (AIMode.DISABLED, AIMode.OFFLINE, AIMode.LOADING):
            self._set_mode(AIMode.LOADING, "Loading model")
            self._load()

    def _fail(self, kind: str, detail: str) -> None:
        self.metrics.errors += 1
        self._record_error(kind, detail)
        self._stop_inference(reason=f"AI failure: {kind}")
        self._attempts += 1
        delay = min(self.env.recovery_initial_s * (2 ** (self._attempts - 1)), self.env.recovery_max_s)
        self._recover_at = time.monotonic() + delay
        self._set_mode(AIMode.ERROR, f"{detail} — Standard Engine continues; retry in {delay:.0f}s")

    def _load(self) -> None:
        cfg = self.config
        state = self.registry.get(cfg.model_id)
        if state is None:
            self._go_offline(f"Unknown model '{cfg.model_id}'")
            return
        state = self.registry.validate(cfg.model_id)
        if state.status in (ModelStatus.MISSING, ModelStatus.INVALID):
            self._record_error("model", state.detail)
            self._go_offline(state.detail)
            return
        needed = state.spec.size_bytes / 1_048_576 * 4 + 64  # weights + activations, conservative
        free = self._memory_available_mb()
        if free < max(self.env.min_free_memory_mb, needed):
            self._fail("memory", f"Insufficient memory to load model ({free:.0f} MB free, {needed:.0f} MB needed)")
            return
        provider = self._provider_factory(self.env, cfg)
        try:
            provider.initialize()
            provider.load_model(str(state.path))
            health = provider.health_check()
            if not health.healthy:
                raise AIProviderError(health.detail)
        except AIProviderError as exc:
            provider.shutdown()
            self.registry.mark(cfg.model_id, ModelStatus.INVALID, str(exc))
            self._fail("model", f"Model load failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            provider.shutdown()
            self._fail("model", f"Model load failed: {type(exc).__name__}: {exc}")
            return
        self.registry.mark(cfg.model_id, ModelStatus.LOADED, "In use")
        self.warnings = list(health.warnings)
        if cfg.attributes == AttributeProviderKind.CLAUDE and not (
            self._attr_provider and self._attr_provider.available
        ):
            self.warnings.append("Claude attribute provider selected but ACI_AGENT_AI_API_KEY is not set")
        if cfg.attributes == AttributeProviderKind.CLAUDE and importlib.util.find_spec("anthropic") is None:
            self.warnings.append(
                "Claude selected but the 'anthropic' package is not installed (uv sync --extra claude)"
            )
        recovered = self._attempts > 0
        self._attempts, self._recover_at = 0, None
        with self._lock:
            self._provider = provider
            self._generation += 1
            gen = self._generation
        threading.Thread(
            target=self._inference_loop, args=(gen, provider), name=f"ai-inference-{gen}", daemon=True
        ).start()
        mode = AIMode.DEGRADED if self.warnings else AIMode.ACTIVE
        self._set_mode(mode, ("Recovered — " if recovered else "") + ("; ".join(self.warnings) or "Running"))

    def _go_offline(self, reason: str) -> None:
        self._offline_check_at = time.monotonic() + 30
        self._set_mode(AIMode.OFFLINE, f"{reason} — Standard Engine continues")

    def _stop_inference(self, reason: str) -> None:
        with self._lock:
            self._generation += 1  # the running loop (or a hung call) is abandoned
            provider, self._provider = self._provider, None
            self._inflight = None
            cams = list(self._cams.items())
        if provider is not None:
            if (
                self.registry.get(self.config.model_id)
                and self.registry.get(self.config.model_id).status == ModelStatus.LOADED
            ):
                self.registry.mark(self.config.model_id, ModelStatus.VALID, "Checksum verified")
        for cid, st in cams:
            self._end_all_tracks(cid, st, reason)
            self._unsubscribe(st)

    # ------------------------------------------------------------------ inference

    def _cameras_list(self) -> list:
        ts, cams = self._camera_cache
        if time.monotonic() - ts > 1.0:
            cams = self._cameras.repo.list()
            self._camera_cache = (time.monotonic(), cams)
        return cams

    def _inference_loop(self, gen: int, provider: AIProvider) -> None:
        try:
            while not self._stop.is_set() and gen == self._generation:
                worked = False
                for cam in self._cameras_list():
                    if gen != self._generation:
                        return
                    if not cam.options.get("ai_enabled", True):
                        self._release_camera(cam.id, "AI disabled for this camera")
                        continue
                    worked |= self._step_camera(cam.id, provider, gen)
                if not worked:
                    time.sleep(0.02)
        except AIProviderError as exc:
            if gen == self._generation:
                self._fail("inference", str(exc))
        except Exception as exc:  # noqa: BLE001
            if gen == self._generation:
                logger.exception("AI inference loop crashed")
                self._fail("inference", f"{type(exc).__name__}: {exc}")

    def _camera(self, camera_id: str) -> CameraAIState:
        st = self._cams.get(camera_id)
        if st is None:
            cfg = self.config
            st = CameraAIState(
                ByteTracker(
                    camera_id,
                    high_threshold=cfg.track_high_threshold,
                    buffer_frames=cfg.track_buffer_frames,
                    min_hits=cfg.min_track_hits,
                )
            )
            self._cams[camera_id] = st
        return st

    def _release_camera(self, camera_id: str, reason: str) -> None:
        st = self._cams.get(camera_id)
        if st is not None and (st.handle is not None or st.tracker.tracks):
            self._end_all_tracks(camera_id, st, reason)
            self._unsubscribe(st)

    def _unsubscribe(self, st: CameraAIState) -> None:
        if st.handle is not None and st.queue is not None:
            st.handle.unsubscribe(st.queue)
        st.handle = st.queue = None

    def _step_camera(self, camera_id: str, provider: AIProvider, gen: int) -> bool:
        st = self._camera(camera_id)
        handle = self._cameras.stream_handle(camera_id)
        if handle is None:
            if st.handle is not None:
                self._end_all_tracks(camera_id, st, "camera stream stopped")
                self._unsubscribe(st)
            return False
        if handle is not st.handle:
            self._unsubscribe(st)
            st.handle, st.queue = handle, handle.subscribe(1)
        now = time.monotonic()
        if now < st.next_due:
            return False
        frame = st.queue.get(timeout=0)
        if frame is None:
            # The capture worker is alive (reconnecting) but no video arrives: end open person tracks
            # instead of leaving them "present" forever.
            if st.last_frame_at is not None and now - st.last_frame_at > STREAM_STALE_S and st.tracker.tracks:
                self._end_all_tracks(camera_id, st, "camera stream lost")
                st.last_frame_at = None
            return False
        st.last_frame_at = now
        st.next_due = now + 1.0 / self.config.process_fps

        self._inflight = (time.monotonic(), gen)
        started = time.perf_counter()
        detections = provider.predict(frame.image)
        latency = (time.perf_counter() - started) * 1000
        self._inflight = None
        if gen != self._generation:
            return False  # abandoned (timeout / disabled) while we were inferring
        h, w = frame.image.shape[:2]
        dets = [d for d in detections if d.h / h >= self.config.min_box_height]
        self.metrics.record(latency, sum(1 for d in dets if d.confidence >= self.config.confidence_threshold))
        self._track(camera_id, st, frame.image, dets, w, h)
        return True

    # ------------------------------------------------------------------ tracking & events

    def _track(
        self, camera_id: str, st: CameraAIState, image: np.ndarray, dets: list[Detection], w: int, h: int
    ) -> None:
        cfg = self.config
        upd = st.tracker.update(dets, time.monotonic())
        st.frame_size = (w, h)
        for t in upd.entered:
            st.people_seen += 1
            attrs = AppearanceAttributes()
            if cfg.attributes != AttributeProviderKind.OFF:
                attrs = analyse_colors(image, t.box)
                if (
                    cfg.attributes == AttributeProviderKind.CLAUDE
                    and self._attr_provider
                    and self._attr_provider.available
                ):
                    x, y, bw, bh = (int(round(v)) for v in t.box)
                    crop = image[max(0, y) : min(h, y + bh), max(0, x) : min(w, x + bw)].copy()
                    self._attr_provider.submit(camera_id, t.track_id, crop)
            st.attributes[t.track_id] = attrs
            self._emit(camera_id, "person_detected", t, w, h, {"attributes": attrs.to_dict()})
        for t in upd.exited:
            self._emit_exit(camera_id, st, t, w, h, "left view")

        views = [_TrackView(t.track_id, Box(*t.box)) for t in upd.confirmed]
        ended = [_TrackView(t.track_id, Box(*t.box)) for t in upd.exited]
        lines = [ln for ln in self._vision_repo.lines_for_engine(camera_id) if ln.is_enabled]
        by_id = {t.track_id: t for t in upd.confirmed}
        for ev in st.lines.update(lines, views, ended, w, h):
            t = by_id.get(ev.track_id)
            if t is not None:
                self._emit(camera_id, "person_crossed_line", t, w, h, dict(ev.metadata), line_id=ev.line_id)

        st.people = [
            {
                "track_id": t.track_id,
                "box": (round(t.box[0] / w, 4), round(t.box[1] / h, 4), round(t.box[2] / w, 4), round(t.box[3] / h, 4)),
                "confidence": round(t.confidence, 2),
                "direction": self._direction(t, w, h),
                "duration_s": round(t.duration_s, 1),
                "attributes": st.attributes.get(t.track_id, AppearanceAttributes()).summary(),
            }
            for t in upd.confirmed
        ]

    @staticmethod
    def _direction(t: PersonTrack, w: int, h: int) -> str | None:
        if len(t.history) < 2:
            return None
        (x0, y0), (x1, y1) = t.history[0], t.history[-1]
        if ((x1 - x0) / w) ** 2 + ((y1 - y0) / h) ** 2 < 0.02**2:
            return None
        return compass(x1 - x0, y1 - y0)

    def _emit_exit(self, camera_id: str, st: CameraAIState, t: PersonTrack, w: int, h: int, reason: str) -> None:
        attrs = st.attributes.pop(t.track_id, AppearanceAttributes())
        self._emit(camera_id, "person_left", t, w, h, {"reason": reason, "attributes": attrs.to_dict()})

    def _end_all_tracks(self, camera_id: str, st: CameraAIState, reason: str) -> None:
        w, h = st.frame_size or (1, 1)
        for t in st.tracker.reset():
            self._emit_exit(camera_id, st, t, w, h, reason)
        st.people = []

    def _emit(
        self, camera_id: str, event_type: str, t: PersonTrack, w: int, h: int, extra: dict, line_id: str | None = None
    ) -> None:
        x, y, bw, bh = t.box
        meta = {
            "confidence": round(t.max_confidence, 3),
            "box": (round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)),
            "heading": self._direction(t, w, h),
            "duration_s": round(t.duration_s, 1),
            "detector": self.config.model_id,
            **extra,
        }
        self._store.append(
            Event(
                camera_id=camera_id,
                event_type=event_type,
                source="ai",
                track_id=t.track_id,
                line_id=line_id,
                metadata=meta,
            )
        )

    # ------------------------------------------------------------------ attributes (cloud)

    def _configure_attribute_provider(self) -> None:
        want = self.config.attributes == AttributeProviderKind.CLAUDE
        if want and self._attr_provider is None:
            self._attr_provider = self._claude_factory(
                api_key=self.env.api_key.get_secret_value(),
                model=self.env.claude_model,
                timeout_s=self.env.claude_timeout_s,
                max_calls_per_minute=self.env.claude_max_calls_per_minute,
            )
            self._attr_provider.start(self._on_cloud_attributes)
        elif not want and self._attr_provider is not None:
            self._attr_provider.stop()
            self._attr_provider = None

    def _on_cloud_attributes(self, camera_id: str, track_id: str, attrs: AppearanceAttributes) -> None:
        st = self._cams.get(camera_id)
        if st is None:
            return
        current = st.attributes.get(track_id)
        if current is not None:  # track may already have left; then the result is dropped
            st.attributes[track_id] = current.merge(attrs)
