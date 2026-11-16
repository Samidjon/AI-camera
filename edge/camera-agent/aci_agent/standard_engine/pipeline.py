"""Vision pipeline: one analysis thread per camera, plus a supervisor.

    capture worker ──FrameQueue(2, drop-oldest)──► VisionWorker
        detector → motion regions → tracker → zones / lines → events → EventStore (async)
                                                         └──► VisionState (overlays, API)

Capture never waits for analysis: the queue is tiny and drops the oldest frame, and the
worker additionally skips frames to honour ``process_fps``. If analysis fails, the error stays
inside that camera's vision thread; capture and the other cameras are unaffected.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from aci_agent.events.models import Event, EventType
from aci_agent.events.store import EventStore
from aci_agent.monitoring.resources import ResourceMonitor
from aci_agent.standard_engine.config import VisionConfig
from aci_agent.standard_engine.motion import MotionResult, create_detector
from aci_agent.standard_engine.rules import CrossingLine, LineCrossingCounter, RuleEvent, Zone, ZoneMonitor
from aci_agent.storage.vision_repository import VisionRepository
from aci_agent.tracking.motion_tracker import MotionTrack, MotionTracker

logger = logging.getLogger("aci.agent.vision")


@dataclass(slots=True)
class TrackView:
    track_id: str
    box: tuple[float, float, float, float]  # normalized x, y, w, h
    direction: str | None
    duration_s: float
    trail: list[tuple[float, float]]  # normalized centroids


@dataclass(slots=True)
class VisionState:
    status: str = "starting"  # starting | waiting_for_video | warming_up | active | paused_memory | disabled | error
    motion_active: bool = False
    motion_since: datetime | None = None
    tracks: list[TrackView] = field(default_factory=list)
    regions: list[tuple[float, float, float, float]] = field(default_factory=list)  # normalized boxes
    mask: np.ndarray | None = None  # small binary motion mask (motion-regions overlay)
    frame_size: tuple[int, int] | None = None
    zone_occupancy: dict[str, int] = field(default_factory=dict)
    line_counts: dict[str, dict[str, int]] = field(default_factory=dict)  # this session
    motion_ratio: float = 0.0
    lighting_changes: int = 0
    frames_analysed: int = 0
    frames_skipped: int = 0
    stream_interruptions: int = 0
    processing_ms: float | None = None
    analysis_fps: float | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class VisionWorker(threading.Thread):
    STALE_AFTER_S = 3.0

    def __init__(self, camera_id: str, camera_manager, repo: VisionRepository, store: EventStore) -> None:
        super().__init__(name=f"vision-{camera_id[:8]}", daemon=True)
        self.camera_id = camera_id
        self._cameras = camera_manager
        self._repo = repo
        self._store = store
        self._stop_event = threading.Event()
        self._reload = threading.Event()
        self._reload.set()
        self.paused = False
        self._lock = threading.Lock()
        self._state = VisionState()
        self._handle = None
        self._queue = None
        self.config = VisionConfig()
        self._zones: list[Zone] = []
        self._lines: list[CrossingLine] = []

    # ------------------------------------------------------------------ control

    def request_reload(self) -> None:
        self._reload.set()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
        return not self.is_alive()

    def state(self) -> VisionState:
        with self._lock:
            return VisionState(**{f: getattr(self._state, f) for f in VisionState.__dataclass_fields__})

    @property
    def zones(self) -> list[Zone]:
        return list(self._zones)

    @property
    def lines(self) -> list[CrossingLine]:
        return list(self._lines)

    # ------------------------------------------------------------------ thread

    def run(self) -> None:
        self._setup()
        last_frame_at: float | None = None
        last_processed = 0.0
        ema_ms: float | None = None
        ema_interval: float | None = None
        try:
            while not self._stop_event.is_set():
                if self._reload.is_set():
                    self._setup()
                if not self.config.enabled:
                    self._set(status="disabled")
                    self._stop_event.wait(1.0)
                    continue
                if self.paused:
                    self._set(status="paused_memory")
                    self._stop_event.wait(1.0)
                    continue
                if not self._ensure_subscription():
                    if last_frame_at is not None:
                        self._on_stream_lost("camera stopped")
                        last_frame_at = None
                    self._set(status="waiting_for_video")
                    self._stop_event.wait(0.5)
                    continue

                frame = self._queue.get(timeout=1.0)
                now = time.monotonic()
                if frame is None:
                    if last_frame_at is not None and now - last_frame_at > self.STALE_AFTER_S:
                        self._on_stream_lost("no video frames")
                        last_frame_at = None
                    if last_frame_at is None:
                        self._set(status="waiting_for_video")
                    self._idle_tick(now)
                    continue
                last_frame_at = now
                if now - last_processed < 1.0 / self.config.process_fps:
                    with self._lock:
                        self._state.frames_skipped += 1
                    continue
                if last_processed:
                    interval = now - last_processed
                    ema_interval = interval if ema_interval is None else 0.2 * interval + 0.8 * ema_interval
                last_processed = now
                started = time.perf_counter()
                try:
                    self._process(frame.image, now)
                except Exception as exc:  # contained: one bad frame / bug never kills the thread
                    logger.exception("Vision error on camera %s", self.camera_id)
                    self._set(status="error", last_error=f"{type(exc).__name__}: {exc}"[:200])
                    continue
                ms = (time.perf_counter() - started) * 1000
                ema_ms = ms if ema_ms is None else 0.2 * ms + 0.8 * ema_ms
                with self._lock:
                    self._state.processing_ms = round(ema_ms, 1)
                    self._state.analysis_fps = round(1 / ema_interval, 1) if ema_interval else None
        finally:
            self._finish_motion(time.monotonic(), reason="vision stopped")
            self._unsubscribe()

    # ------------------------------------------------------------------ setup

    def _setup(self) -> None:
        self._reload.clear()
        cfg = self._repo.get_config(self.camera_id)
        self._zones = self._repo.zones_for_engine(self.camera_id)
        self._lines = self._repo.lines_for_engine(self.camera_id)
        rebuild = getattr(self, "_detector", None) is None or cfg.model_dump() != self.config.model_dump()
        self.config = cfg
        if rebuild:
            self._detector = create_detector(cfg)
            self._tracker = MotionTracker(self.camera_id, cfg.track_max_distance, cfg.track_max_missed)
            self._zone_monitor = ZoneMonitor()
            self._line_counter = LineCrossingCounter()
            self._motion_active = False
            self._motion_started_mono = 0.0
            self._last_motion_mono = 0.0
            self._episode_max_objects = 0
            self._episode_peak_ratio = 0.0
            self._activity_window_start = time.monotonic()
            self._activity_active_s = 0.0
            self._activity_max_objects = 0
            self._activity_last_tick: float | None = None

    def _ensure_subscription(self) -> bool:
        handle = self._cameras.stream_handle(self.camera_id)
        if handle is None:
            self._unsubscribe()
            return False
        if handle is not self._handle:
            self._unsubscribe()
            self._handle = handle
            self._queue = handle.subscribe(2)  # tiny + drop-oldest = automatic frame skipping
            self._detector.reset()  # a new stream: relearn the background
        return True

    def _unsubscribe(self) -> None:
        if self._handle is not None and self._queue is not None:
            self._handle.unsubscribe(self._queue)
        self._handle = self._queue = None

    # ------------------------------------------------------------------ analysis

    def _process(self, image: np.ndarray, now: float) -> None:
        cfg = self.config
        h, w = image.shape[:2]
        monitor = [z.polygon for z in self._zones if z.role == "monitor" and z.is_enabled]
        exclude = [z.polygon for z in self._zones if z.role == "exclude" and z.is_enabled]
        include = monitor if (cfg.restrict_to_zones and monitor) else None
        result: MotionResult = self._detector.detect(image, include, exclude or None)

        regions = [] if (result.lighting_change or result.warming_up) else result.regions
        upd = self._tracker.update(regions, w, h, now)
        rule_events: list[RuleEvent] = []
        rule_events += self._zone_monitor.update(self._zones, upd.active, upd.ended, w, h)
        rule_events += self._line_counter.update(self._lines, upd.active, upd.ended, w, h)
        tracks_by_id = {t.track_id: t for t in upd.active + upd.ended}
        for ev in rule_events:
            self._emit(ev.event_type, track=tracks_by_id.get(ev.track_id), size=(w, h),
                       zone_id=ev.zone_id, line_id=ev.line_id, extra=ev.metadata)  # fmt: skip

        # Motion episode state machine (debounced end).
        n = len(upd.active)
        if n:
            self._last_motion_mono = now
            self._episode_max_objects = max(self._episode_max_objects, n)
            self._episode_peak_ratio = max(self._episode_peak_ratio, result.motion_ratio)
            if not self._motion_active:
                self._motion_active = True
                self._motion_started_mono = now
                self._emit(EventType.MOTION_STARTED, extra={
                    "objects": n, "motion_ratio": round(result.motion_ratio, 4),
                    "boxes": [_norm(t.box, w, h) for t in upd.active[:10]],
                })  # fmt: skip
                with self._lock:
                    self._state.motion_since = datetime.now(UTC)
        elif self._motion_active and now - self._last_motion_mono >= cfg.motion_end_delay_s:
            self._finish_motion(now, reason="no motion")

        self._account_activity(now, n)

        with self._lock:
            s = self._state
            s.status = "warming_up" if result.warming_up else "active"
            s.motion_active = self._motion_active
            s.tracks = [
                TrackView(
                    t.track_id,
                    _norm(t.box, w, h),
                    t.direction(w, h),
                    round(t.duration_s, 1),
                    [(x / w, y / h) for x, y in list(t.history)[-16:]],
                )  # fmt: skip
                for t in upd.active
            ]
            s.regions = [_norm(r.box, w, h) for r in result.regions]
            s.mask = result.mask
            s.frame_size = (w, h)
            s.zone_occupancy = self._zone_monitor.occupancy()
            s.line_counts = {k: dict(v) for k, v in self._line_counter.counts.items()}
            s.motion_ratio = round(result.motion_ratio, 4)
            s.frames_analysed += 1
            if result.lighting_change:
                s.lighting_changes += 1
            s.last_error = None
            s.updated_at = datetime.now(UTC)

    def _idle_tick(self, now: float) -> None:
        if self._motion_active and now - self._last_motion_mono >= self.config.motion_end_delay_s:
            self._finish_motion(now, reason="no video" if self._handle is None else "no motion")
        self._account_activity(now, 0)

    def _on_stream_lost(self, reason: str) -> None:
        """Video stopped: close open motion/zone state cleanly; relearn background on resume."""
        now = time.monotonic()
        ended = self._tracker.reset()
        for ev in self._zone_monitor.update(self._zones, [], ended, 1, 1):
            ev.metadata["reason"] = reason
            self._emit(ev.event_type, zone_id=ev.zone_id, extra=ev.metadata, track_id=ev.track_id)
        self._zone_monitor.reset()
        self._line_counter.reset()
        self._finish_motion(now, reason=reason)
        self._detector.reset()
        with self._lock:
            self._state.stream_interruptions += 1
            self._state.tracks, self._state.regions, self._state.mask = [], [], None
            self._state.zone_occupancy = {}

    def _finish_motion(self, now: float, reason: str) -> None:
        if not getattr(self, "_motion_active", False):
            return
        self._motion_active = False
        self._emit(EventType.MOTION_ENDED, extra={
            "duration_s": round(max(0.0, self._last_motion_mono - self._motion_started_mono), 1),
            "max_objects": self._episode_max_objects,
            "peak_motion_ratio": round(self._episode_peak_ratio, 4),
            "reason": reason,
        })  # fmt: skip
        self._episode_max_objects, self._episode_peak_ratio = 0, 0.0
        with self._lock:
            self._state.motion_active = False
            self._state.motion_since = None

    def _account_activity(self, now: float, objects: int) -> None:
        if self._activity_last_tick is not None and self._motion_active:
            self._activity_active_s += now - self._activity_last_tick
        self._activity_last_tick = now
        self._activity_max_objects = max(self._activity_max_objects, objects)
        window = now - self._activity_window_start
        if window >= self.config.activity_interval_s:
            if self._activity_active_s > 0:
                self._emit(EventType.CAMERA_MOTION_ACTIVITY, extra={
                    "interval_s": round(window, 1),
                    "active_s": round(self._activity_active_s, 1),
                    "active_ratio": round(min(1.0, self._activity_active_s / window), 3),
                    "max_objects": self._activity_max_objects,
                })  # fmt: skip
            self._activity_window_start = now
            self._activity_active_s = 0.0
            self._activity_max_objects = 0

    def _emit(self, event_type, *, track: MotionTrack | None = None, size=None, zone_id=None, line_id=None,
              extra: dict | None = None, track_id: str | None = None) -> None:  # fmt: skip
        meta = dict(extra or {})
        if track is not None and size is not None:
            w, h = size
            meta.update(
                heading=track.direction(w, h),  # compass heading of the motion track
                box=_norm(track.box, w, h),
                track_duration_s=round(track.duration_s, 1),
                area_ratio=round(track.max_area / (w * h), 4),
            )
        meta["detector"] = self.config.algorithm.value
        self._store.append(Event(
            camera_id=self.camera_id, event_type=str(event_type), zone_id=zone_id, line_id=line_id,
            track_id=track.track_id if track else track_id, metadata=meta,
        ))  # fmt: skip

    def _set(self, **changes) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(self._state, k, v)


def _norm(box, w: float, h: float) -> tuple[float, float, float, float]:
    return (round(box.x / w, 4), round(box.y / h, 4), round(box.w / w, 4), round(box.h / h, 4))


class VisionManager:
    """Owns vision workers and protects the device: if the agent's memory exceeds the limit,
    analysis pauses (capture continues) until usage drops below 90 % of the limit."""

    def __init__(
        self,
        camera_manager,
        repo: VisionRepository,
        store: EventStore,
        resources: ResourceMonitor | None = None,
        max_rss_mb: float = 1024,
        supervisor_interval_s: float = 2.0,
    ) -> None:
        self._cameras = camera_manager
        self.repo = repo
        self.store = store
        self.resources = resources or ResourceMonitor()
        self.max_rss_mb = max_rss_mb
        self._interval = supervisor_interval_s
        self._workers: dict[str, VisionWorker] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.memory_paused = False
        self._supervisor = threading.Thread(target=self._supervise, name="vision-supervisor", daemon=True)

    def start(self) -> None:
        self.sync()
        self._supervisor.start()

    def sync(self) -> None:
        """Ensure exactly one worker per existing camera (workers wait for video by themselves)."""
        ids = {c.id for c in self._cameras.repo.list()}
        with self._lock:
            for cid in ids - set(self._workers):
                worker = VisionWorker(cid, self._cameras, self.repo, self.store)
                worker.paused = self.memory_paused
                self._workers[cid] = worker
                worker.start()
            removed = [self._workers.pop(cid) for cid in set(self._workers) - ids]
        for w in removed:
            w.stop()

    def reload(self, camera_id: str) -> None:
        with self._lock:
            worker = self._workers.get(camera_id)
        if worker:
            worker.request_reload()
        else:
            self.sync()

    def worker(self, camera_id: str) -> VisionWorker | None:
        with self._lock:
            return self._workers.get(camera_id)

    def state(self, camera_id: str) -> VisionState | None:
        w = self.worker(camera_id)
        return w.state() if w else None

    def _supervise(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.sync()
                rss = self.resources.rss_mb()
                if not self.memory_paused and rss > self.max_rss_mb:
                    self._set_paused(True)
                    logger.error("Memory %.0f MB above limit %.0f MB — vision analysis paused", rss, self.max_rss_mb)
                elif self.memory_paused and rss < self.max_rss_mb * 0.9:
                    self._set_paused(False)
                    logger.warning("Memory back to %.0f MB — vision analysis resumed", rss)
            except Exception:  # noqa: BLE001
                logger.exception("Vision supervisor error")

    def _set_paused(self, paused: bool) -> None:
        self.memory_paused = paused
        with self._lock:
            for w in self._workers.values():
                w.paused = paused

    def stats(self) -> dict:
        with self._lock:
            states = [w.state() for w in self._workers.values()]
        return {
            "workers": len(states),
            "active": sum(1 for s in states if s.status == "active"),
            "motion_now": sum(1 for s in states if s.motion_active),
            "memory_paused": self.memory_paused,
            "max_rss_mb": self.max_rss_mb,
        }

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._supervisor.is_alive():
            self._supervisor.join(timeout)
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w._stop_event.set()
        for w in workers:
            w.stop(timeout)
