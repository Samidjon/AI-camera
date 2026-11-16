"""One worker thread per camera: connect → read frames → detect loss → reconnect with backoff.

Isolation guarantees:
* every exception is caught inside the thread, so one broken camera never stops another;
* the capture device is always released in ``finally``;
* frames go to bounded containers only (single-slot preview buffer, drop-oldest queues).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from aci_agent.camera.models import CameraConfig, CameraRuntime, CameraSecret, CameraStatus, FailureKind, SourceType
from aci_agent.camera.rtsp_probe import ProbeResult, probe_rtsp
from aci_agent.camera.sources import CaptureDevice, CaptureFactory, CaptureTarget, SourceError, resolve_target
from aci_agent.configuration.settings import StreamSettings
from aci_agent.monitoring.logging import redact
from aci_agent.video.connection_monitor import ConnectionMonitor
from aci_agent.video.frame_buffer import Frame, FrameQueue, LatestFrameBuffer

logger = logging.getLogger("aci.agent.worker")

StatusCallback = Callable[[str, CameraRuntime], None]
MAX_CONSECUTIVE_READ_FAILURES = 3


class _ReadOutcome:
    def __init__(self, failure: FailureKind | None, message: str, end_of_file: bool = False) -> None:
        self.failure, self.message, self.end_of_file = failure, message, end_of_file


class StreamWorker(threading.Thread):
    def __init__(
        self,
        config: CameraConfig,
        secret: CameraSecret | None,
        factory: CaptureFactory,
        settings: StreamSettings,
        on_status: StatusCallback | None = None,
        probe: Callable[[str, str, str, float], ProbeResult] = probe_rtsp,
        resolver: Callable[[CameraConfig, CameraSecret | None, float], CaptureTarget] = resolve_target,
    ) -> None:
        super().__init__(name=f"camera-{config.id[:8]}", daemon=True)
        self.config = config
        self._secret = secret
        self._factory = factory
        self._settings = settings
        self._on_status = on_status
        self._probe = probe
        self._resolver = resolver
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._runtime = CameraRuntime(status=CameraStatus.CONNECTING)
        self.monitor = ConnectionMonitor(settings.stale_after_s)
        self.buffer = LatestFrameBuffer()
        self._queues: list[FrameQueue] = []
        self._seq = 0
        max_fps = config.options.get("max_fps")
        self._min_publish_interval = 1.0 / float(max_fps) if max_fps else 0.0

    # ------------------------------------------------------------------ public API

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop(self, timeout: float = 5.0) -> bool:
        """Signal the thread and wait. Returns False if it is still blocked in the decoder."""
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
        return not self.is_alive()

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    def subscribe(self, maxsize: int) -> FrameQueue:
        queue = FrameQueue(maxsize)
        with self._lock:
            self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: FrameQueue) -> None:
        with self._lock:
            if queue in self._queues:
                self._queues.remove(queue)

    def snapshot(self) -> CameraRuntime:
        """Runtime state with real-time stall detection applied."""
        with self._lock:
            state = replace(self._runtime)
        state.fps = self.monitor.fps if state.status == CameraStatus.ONLINE else None
        if state.status == CameraStatus.ONLINE and self.monitor.is_stale():
            age = self.monitor.seconds_since_frame() or 0
            state.status = CameraStatus.RECONNECTING
            state.fps = None
            state.last_error = f"No frames received for {age:.0f}s"
            state.failure_kind = FailureKind.STREAM_LOST
        return state

    # ------------------------------------------------------------------ thread body

    def run(self) -> None:
        attempt = 0
        first = True
        try:
            while not self._stop_event.is_set():
                self._set(status=CameraStatus.CONNECTING if first else CameraStatus.RECONNECTING)
                outcome = self._connect_and_stream()
                if self._stop_event.is_set():
                    break
                if outcome.end_of_file:
                    self._set(status=CameraStatus.OFFLINE, last_error="End of video file", failure_kind=None)
                    break
                if self._runtime.status == CameraStatus.ONLINE or outcome.failure == FailureKind.STREAM_LOST:
                    attempt = 0  # was streaming: restart the backoff ladder
                first = False
                attempt += 1
                transient = outcome.failure is None or outcome.failure.is_transient
                self._set(
                    status=CameraStatus.RECONNECTING if transient else CameraStatus.ERROR,
                    last_error=outcome.message,
                    failure_kind=outcome.failure,
                    reconnect_attempts=attempt,
                )
                logger.warning(
                    "Camera %s: %s (attempt %d)", self.config.name, redact(outcome.message), attempt,
                    extra={"camera_id": self.config.id, "failure": str(outcome.failure)},
                )  # fmt: skip
                self._stop_event.wait(self._backoff(attempt, transient))
        except Exception:  # last line of defence: never let a camera thread die silently
            logger.exception("Camera %s worker crashed", self.config.name, extra={"camera_id": self.config.id})
            self._set(status=CameraStatus.ERROR, last_error="Internal worker error", failure_kind=FailureKind.UNKNOWN)
        finally:
            self.buffer.close()
            if self._runtime.status not in (CameraStatus.ERROR, CameraStatus.OFFLINE) or self._stop_event.is_set():
                self._set(status=CameraStatus.OFFLINE, fps=None)

    def _backoff(self, attempt: int, transient: bool) -> float:
        s = self._settings
        base = min(s.reconnect_initial_s * (2 ** (attempt - 1)), s.reconnect_max_s)
        if not transient:
            base = max(base, min(10.0, s.reconnect_max_s))  # non-transient: don't hammer the device
        return base * random.uniform(0.8, 1.2)

    def _connect_and_stream(self) -> _ReadOutcome:
        device: CaptureDevice | None = None
        try:
            target = self._resolver(self.config, self._secret, self._settings.open_timeout_s)
            try:
                device = self._factory.open(target, self._settings.open_timeout_s, self._settings.read_timeout_s)
            except SourceError as exc:
                return self._classify_open_failure(target, exc)
            nominal = device.nominal_fps()
            self._set(nominal_fps=nominal)
            return self._read_frames(device, target, nominal)
        except SourceError as exc:
            return _ReadOutcome(exc.failure, str(exc))
        except Exception as exc:  # unexpected errors stay inside this camera
            logger.exception("Camera %s: unexpected error", self.config.name, extra={"camera_id": self.config.id})
            return _ReadOutcome(FailureKind.UNKNOWN, f"Unexpected error: {type(exc).__name__}")
        finally:
            if device is not None:
                try:
                    device.release()
                except Exception:  # noqa: BLE001
                    logger.warning("Camera %s: release failed", self.config.name)
            self.monitor.reset()

    def _classify_open_failure(self, target: CaptureTarget, exc: SourceError) -> _ReadOutcome:
        if target.probe_url and exc.failure == FailureKind.UNREACHABLE:
            secret = self._secret or CameraSecret()
            # Diagnose with the credential-free URL; credentials are passed separately.
            result = self._probe(target.probe_url, secret.username, secret.password, self._settings.open_timeout_s)
            if not result.ok and result.failure:
                return _ReadOutcome(result.failure, result.message)
            return _ReadOutcome(FailureKind.UNSUPPORTED_FORMAT, "Camera answers RTSP but the stream can't be decoded")
        return _ReadOutcome(exc.failure, str(exc))

    def _read_frames(self, device: CaptureDevice, target: CaptureTarget, nominal_fps: float | None) -> _ReadOutcome:
        is_file = target.source_type == SourceType.FILE
        frame_interval = 1.0 / nominal_fps if (is_file and nominal_fps) else 0.0
        failures = 0
        got_any = False
        last_publish = 0.0
        last_persist = 0.0
        next_due = time.monotonic()

        while not self._stop_event.is_set():
            ok, image = device.read()
            if not ok:
                if is_file and got_any:
                    if target.loop and device.rewind():
                        continue
                    return _ReadOutcome(None, "End of video file", end_of_file=True)
                failures += 1
                if failures >= MAX_CONSECUTIVE_READ_FAILURES:
                    if got_any:
                        return _ReadOutcome(FailureKind.STREAM_LOST, "Video stream lost")
                    if is_file:
                        return _ReadOutcome(FailureKind.UNSUPPORTED_FORMAT, "Video file can't be decoded")
                    return _ReadOutcome(FailureKind.UNSUPPORTED_FORMAT, "Connected, but no decodable video frames")
                self._stop_event.wait(0.05)
                continue

            failures = 0
            now_mono = time.monotonic()
            now = datetime.now(UTC)
            self.monitor.on_frame(now_mono)
            if not got_any:
                got_any = True
                self._set(
                    status=CameraStatus.ONLINE,
                    last_connected_at=now,
                    last_error=None,
                    failure_kind=None,
                    reconnect_attempts=0,
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                )
                last_persist = now_mono
            with self._lock:
                self._runtime.frames_received += 1
                self._runtime.last_frame_at = now
                self._runtime.width, self._runtime.height = int(image.shape[1]), int(image.shape[0])

            if now_mono - last_publish >= self._min_publish_interval:
                last_publish = now_mono
                self._seq += 1
                frame = Frame(self._seq, now.timestamp(), image)
                self.buffer.publish(frame)
                with self._lock:
                    queues = list(self._queues)
                for q in queues:
                    q.put(frame)
                with self._lock:
                    self._runtime.frames_dropped = sum(q.dropped for q in self._queues)

            if now_mono - last_persist >= self._settings.status_persist_interval_s:
                last_persist = now_mono
                self._notify()

            if frame_interval:  # files decode instantly: pace them to real time
                next_due = max(next_due + frame_interval, now_mono)
                self._stop_event.wait(max(0.0, next_due - time.monotonic()))
        return _ReadOutcome(None, "Stopped")

    # ------------------------------------------------------------------ state helpers

    def _set(self, **changes) -> None:
        with self._lock:
            before = self._runtime.status
            for key, value in changes.items():
                setattr(self._runtime, key, value)
            changed = self._runtime.status != before
        if changed:
            self._notify()

    def _notify(self) -> None:
        if self._on_status:
            try:
                self._on_status(self.config.id, self.snapshot())
            except Exception:  # noqa: BLE001 — persistence problems must not break capture
                logger.exception("Status callback failed for camera %s", self.config.id)
