"""Camera Manager: the single entry point for camera lifecycle on the edge.

Responsibilities: validated CRUD (with credentials split out and stored encrypted),
connect / disconnect, test connection, one independent worker per camera, automatic
reconnection (inside workers), group management and clean shutdown.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from aci_agent.camera.models import CameraConfig, CameraRuntime, CameraSecret, CameraStatus, FailureKind, SourceType
from aci_agent.camera.rtsp_probe import ProbeResult, probe_rtsp
from aci_agent.camera.sources import (
    CaptureFactory,
    CaptureTarget,
    OpenCVCaptureFactory,
    SourceError,
    resolve_target,
    split_credentials,
    validate_source,
)
from aci_agent.configuration.settings import AgentSettings
from aci_agent.monitoring.logging import redact
from aci_agent.security.crypto import SecretDecryptionError
from aci_agent.storage.repository import CameraRepository
from aci_agent.video.frame_buffer import FrameQueue, LatestFrameBuffer
from aci_agent.video.stream_worker import StreamWorker

logger = logging.getLogger("aci.agent.manager")

RESTART_FIELDS = ("source_type", "source_uri", "options")


class CameraNotFoundError(Exception):
    pass


class CameraConflictError(Exception):
    pass


class CameraLimitError(Exception):
    pass


@dataclass(slots=True)
class CameraView:
    config: CameraConfig
    runtime: CameraRuntime
    has_credentials: bool


@dataclass(slots=True)
class TestResult:
    ok: bool
    message: str
    failure: FailureKind | None = None
    width: int | None = None
    height: int | None = None
    nominal_fps: float | None = None
    latency_ms: float | None = None
    stream: str | None = None  # credential-free description of what was opened
    details: dict = field(default_factory=dict)


@dataclass(slots=True)
class CameraInput:
    name: str
    source_type: SourceType
    source_uri: str
    location: str | None = None
    group_id: str | None = None
    is_enabled: bool = True
    options: dict = field(default_factory=dict)
    username: str | None = None  # None = keep existing (on update)
    password: str | None = None


class CameraManager:
    def __init__(
        self,
        repo: CameraRepository,
        settings: AgentSettings,
        factory: CaptureFactory | None = None,
        probe: Callable[[str, str, str, float], ProbeResult] = probe_rtsp,
        resolver: Callable[[CameraConfig, CameraSecret | None, float], CaptureTarget] = resolve_target,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.factory = factory or OpenCVCaptureFactory()
        self._probe = probe
        self._resolver = resolver
        self._workers: dict[str, StreamWorker] = {}
        self._lock = threading.RLock()
        self.leaked_workers = 0  # threads that didn't stop in time (blocked in a decoder)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if not self.settings.autostart_cameras:
            return
        for config in self.repo.list():
            if config.is_enabled:
                self._start_worker(config)

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.request_stop()  # signal all first so they stop in parallel
        deadline = time.monotonic() + timeout
        for w in workers:
            if not w.stop(max(0.1, deadline - time.monotonic())):
                self.leaked_workers += 1
                logger.warning("Camera %s did not stop within %.0fs", w.config.name, timeout)

    # ------------------------------------------------------------------ queries

    def list_cameras(self) -> list[CameraView]:
        return [self._view(c) for c in self.repo.list()]

    def get_camera(self, camera_id: str) -> CameraView:
        config = self.repo.get(camera_id)
        if config is None:
            raise CameraNotFoundError(camera_id)
        return self._view(config)

    def runtime(self, config: CameraConfig) -> CameraRuntime:
        with self._lock:
            worker = self._workers.get(config.id)
        if worker is not None:
            # Live worker, or a finished one that keeps its final state (e.g. "End of video file").
            return worker.snapshot()
        persisted = self.repo.persisted_state(config.id)

        def dt(v):
            return datetime.fromisoformat(v) if v else None

        status = CameraStatus.DISABLED if not config.is_enabled else CameraStatus.OFFLINE
        last_status = persisted.get("last_status")
        error = persisted.get("last_error") if last_status == CameraStatus.ERROR.value else None
        return CameraRuntime(
            status=status,
            width=persisted.get("resolution_width"),
            height=persisted.get("resolution_height"),
            nominal_fps=persisted.get("fps"),
            last_connected_at=dt(persisted.get("last_connected_at")),
            last_frame_at=dt(persisted.get("last_frame_at")),
            last_error=error,
        )

    def _view(self, config: CameraConfig) -> CameraView:
        return CameraView(config, self.runtime(config), self.repo.has_secret(config.id))

    def status_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in CameraStatus}
        for view in self.list_cameras():
            counts[view.runtime.status.value] += 1
        return counts

    def frame_buffer(self, camera_id: str) -> LatestFrameBuffer | None:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.buffer if worker is not None and worker.is_alive() else None

    def subscribe(self, camera_id: str, maxsize: int | None = None) -> FrameQueue:
        """Bounded frame feed for analysis modules (Standard Vision Engine, MVP 03)."""
        with self._lock:
            worker = self._workers.get(camera_id)
        if worker is None:
            raise CameraNotFoundError(camera_id)
        return worker.subscribe(maxsize or self.settings.stream.frame_queue_size)

    def active_workers(self) -> int:
        with self._lock:
            return sum(1 for w in self._workers.values() if w.is_alive())

    # ------------------------------------------------------------------ commands

    def _normalize(self, data: CameraInput) -> tuple[CameraConfig, CameraSecret | None, bool]:
        name = data.name.strip()
        if not 1 <= len(name) <= 120:
            raise ValueError("Name must be 1–120 characters")
        source_uri = validate_source(data.source_type, data.source_uri)
        username, password = data.username, data.password
        secret_given = username is not None or password is not None
        if data.source_type in (SourceType.RTSP, SourceType.FFMPEG):
            clean, url_user, url_pw = split_credentials(source_uri)
            if url_user or url_pw:  # credentials typed into the URL are moved to the encrypted store
                source_uri, username, password, secret_given = clean, url_user, url_pw, True
        if data.source_type in (SourceType.USB, SourceType.FILE) and secret_given and (username or password):
            raise ValueError("USB and file sources don't use credentials")
        if data.group_id and self.repo.get_group(data.group_id) is None:
            raise ValueError("Camera group not found")
        options = _clean_options(data.source_type, data.options)
        config = CameraConfig(
            id="",
            name=name,
            source_type=data.source_type,
            source_uri=source_uri,
            location=(data.location or "").strip() or None,
            group_id=data.group_id or None,
            is_enabled=data.is_enabled,
            options=options,
        )
        secret = CameraSecret(username or "", password or "") if secret_given else None
        return config, (secret if secret else None), secret_given

    def create_camera(self, data: CameraInput, connect: bool = True) -> CameraView:
        if self.repo.count() >= self.settings.max_cameras:
            raise CameraLimitError(f"Camera limit reached ({self.settings.max_cameras})")
        config, secret, _ = self._normalize(data)
        created = self.repo.create(config, secret)
        logger.info("Camera added: %s (%s)", created.name, created.source_type, extra={"camera_id": created.id})
        if connect and created.is_enabled:
            self._start_worker(created)
        return self._view(created)

    def update_camera(self, camera_id: str, data: CameraInput) -> CameraView:
        current = self.repo.get(camera_id)
        if current is None:
            raise CameraNotFoundError(camera_id)
        config, secret, secret_given = self._normalize(data)
        config.id = camera_id
        if not secret_given and config.source_type in (SourceType.USB, SourceType.FILE):
            secret_given, secret = True, None  # switching to a local source drops stored credentials
        updated = self.repo.update(config, secret, replace_secret=secret_given)
        needs_restart = secret_given or any(getattr(current, f) != getattr(updated, f) for f in RESTART_FIELDS)
        with self._lock:
            running = camera_id in self._workers and self._workers[camera_id].is_alive()
        if not updated.is_enabled:
            self._stop_worker(camera_id)
        elif running and needs_restart:
            self._stop_worker(camera_id)
            self._start_worker(updated)
        elif running:
            with self._lock:
                self._workers[camera_id].config = updated  # name/location/group: no reconnect needed
        elif not current.is_enabled and updated.is_enabled:
            self._start_worker(updated)
        return self._view(updated)

    def delete_camera(self, camera_id: str) -> None:
        self._stop_worker(camera_id)
        if not self.repo.delete(camera_id):
            raise CameraNotFoundError(camera_id)
        logger.info("Camera deleted", extra={"camera_id": camera_id})

    def connect(self, camera_id: str) -> CameraView:
        config = self.repo.get(camera_id)
        if config is None:
            raise CameraNotFoundError(camera_id)
        if not config.is_enabled:
            raise CameraConflictError("Camera is disabled — enable it before connecting")
        self._start_worker(config)
        return self._view(config)

    def disconnect(self, camera_id: str) -> CameraView:
        config = self.repo.get(camera_id)
        if config is None:
            raise CameraNotFoundError(camera_id)
        self._stop_worker(camera_id)
        self.repo.save_runtime(camera_id, replace(self.runtime(config), status=CameraStatus.OFFLINE, last_error=None))
        return self._view(config)

    # ------------------------------------------------------------------ test connection

    def test_existing(self, camera_id: str) -> TestResult:
        config = self.repo.get(camera_id)
        if config is None:
            raise CameraNotFoundError(camera_id)
        with self._lock:
            worker = self._workers.get(camera_id)
        if worker is not None and worker.is_alive():
            rt = worker.snapshot()
            if rt.status == CameraStatus.ONLINE:
                # Local devices can't be opened twice; report the live stream instead.
                return TestResult(True, "Camera is streaming", None, rt.width, rt.height, rt.nominal_fps, 0.0, None,
                                  {"measured_fps": rt.fps})  # fmt: skip
        try:
            secret = self.repo.get_secret(camera_id)
        except SecretDecryptionError as exc:
            return TestResult(False, str(exc), FailureKind.AUTH_FAILED)
        return self.test_connection(config, secret)

    def test_input(self, data: CameraInput) -> TestResult:
        try:
            config, secret, _ = self._normalize(data)
        except ValueError as exc:
            return TestResult(False, str(exc), FailureKind.INVALID_SOURCE)
        config.id = "test"
        return self.test_connection(config, secret)

    def test_connection(self, config: CameraConfig, secret: CameraSecret | None) -> TestResult:
        """Open the source, read one real frame, release everything. Never leaves resources open."""
        started = time.perf_counter()
        s = self.settings.stream

        def elapsed() -> float:
            return round((time.perf_counter() - started) * 1000, 1)

        try:
            target = self._resolver(config, secret, s.open_timeout_s)
        except SourceError as exc:
            return TestResult(False, str(exc), exc.failure, latency_ms=elapsed())

        if target.probe_url:
            user, pw = (secret.username, secret.password) if secret else ("", "")
            probe = self._probe(target.probe_url, user, pw, s.open_timeout_s)
            if not probe.ok:
                return TestResult(False, probe.message, probe.failure, latency_ms=elapsed(), stream=target.display,
                                  details={"rtsp_status": probe.status_code})  # fmt: skip

        try:
            device = self.factory.open(target, s.open_timeout_s, s.read_timeout_s)
        except SourceError as exc:
            return TestResult(False, str(exc), exc.failure, latency_ms=elapsed(), stream=target.display)
        try:
            for _ in range(5):  # some sources need a few reads before the first decodable frame
                ok, image = device.read()
                if ok and image is not None:
                    return TestResult(
                        True, "Connection OK — video frame received", None, int(image.shape[1]), int(image.shape[0]),
                        device.nominal_fps(), elapsed(), target.display,
                    )  # fmt: skip
            return TestResult(False, "Connected, but no decodable video frames", FailureKind.UNSUPPORTED_FORMAT,
                              latency_ms=elapsed(), stream=target.display)  # fmt: skip
        finally:
            device.release()

    # ------------------------------------------------------------------ groups

    def list_groups(self) -> list[dict]:
        return self.repo.list_groups()

    def create_group(self, name: str, description: str | None) -> dict:
        return self.repo.create_group(_group_name(name), (description or "").strip() or None)

    def update_group(self, group_id: str, name: str, description: str | None) -> dict:
        group = self.repo.update_group(group_id, _group_name(name), (description or "").strip() or None)
        if group is None:
            raise CameraNotFoundError(group_id)
        return group

    def delete_group(self, group_id: str) -> None:
        if not self.repo.delete_group(group_id):
            raise CameraNotFoundError(group_id)

    # ------------------------------------------------------------------ workers

    def _start_worker(self, config: CameraConfig) -> None:
        with self._lock:
            existing = self._workers.get(config.id)
            if existing is not None and existing.is_alive() and not existing.stopping:
                return
            try:
                secret = self.repo.get_secret(config.id)
            except SecretDecryptionError as exc:
                logger.error("Camera %s: %s", config.name, exc, extra={"camera_id": config.id})
                self.repo.save_runtime(config.id, CameraRuntime(status=CameraStatus.ERROR, last_error=str(exc)))
                return
            worker = StreamWorker(
                config, secret, self.factory, self.settings.stream,
                on_status=self._persist_runtime, probe=self._probe, resolver=self._resolver,
            )  # fmt: skip
            self._workers[config.id] = worker
            worker.start()

    def _stop_worker(self, camera_id: str, timeout: float = 5.0) -> None:
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker is not None and not worker.stop(timeout):
            self.leaked_workers += 1
            logger.warning("Camera %s worker still blocked after stop", camera_id)

    def _persist_runtime(self, camera_id: str, runtime: CameraRuntime) -> None:
        try:
            self.repo.save_runtime(camera_id, runtime)
        except Exception as exc:  # noqa: BLE001 — storage issues must not stop capture
            logger.warning("Could not persist status for camera %s: %s", camera_id, redact(str(exc)))


def _group_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("Group name must be 1–120 characters")
    return name


def _clean_options(source_type: SourceType, options: dict) -> dict:
    """Whitelist and bound per-source options."""
    out: dict = {}
    if "max_fps" in options and options["max_fps"] not in (None, ""):
        fps = float(options["max_fps"])
        if not 0.5 <= fps <= 120:
            raise ValueError("max_fps must be between 0.5 and 120")
        out["max_fps"] = fps
    if source_type == SourceType.FILE:
        out["loop"] = bool(options.get("loop", True))
    if source_type == SourceType.ONVIF:
        profile = int(options.get("onvif_profile", 0))
        if not 0 <= profile <= 16:
            raise ValueError("onvif_profile must be 0–16")
        out["onvif_profile"] = profile
    return out
