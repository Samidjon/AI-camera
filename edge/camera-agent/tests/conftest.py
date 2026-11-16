"""Test fixtures.

``FakeFactory`` is a controllable capture backend used to reproduce failures that are hard to
trigger on demand with real hardware: wrong password, unplugged cable, network loss and
recovery, undecodable stream, stalled decoder. Real OpenCV decoding is covered separately with
generated video files (tests/integration/test_real_sources.py) and, optionally, a USB camera.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import numpy as np
import pytest
from fastapi.testclient import TestClient

from aci_agent.api.app import create_app
from aci_agent.camera.manager import CameraManager
from aci_agent.camera.models import FailureKind
from aci_agent.camera.rtsp_probe import ProbeResult
from aci_agent.camera.sources import CaptureTarget, SourceError
from aci_agent.configuration.settings import AgentSettings, StreamSettings
from aci_agent.security.crypto import SecretBox
from aci_agent.storage.database import Database
from aci_agent.storage.repository import CameraRepository

JWT_SECRET = "test-shared-secret-0123456789abcdef"


class FakeSource:
    """Behaviour of one simulated camera. Flip attributes at runtime to simulate events."""

    def __init__(self, width: int = 64, height: int = 48, fps: float = 50.0) -> None:
        self.width, self.height, self.fps = width, height, fps
        self.cable_connected = True  # False → open fails (unreachable) and reads fail
        self.decodable = True  # False → opens, but every read fails
        self.open_error: SourceError | None = None
        self.unblocked = threading.Event()
        self.unblocked.set()  # clear() → reads hang (stalled decoder)
        self.opens = 0
        self.releases = 0
        self.lock = threading.Lock()
        # Optional scene renderer: frame index -> BGR image (moving objects for vision tests).
        self.scene: Callable[[int], np.ndarray] | None = None
        self.frame_index = 0

    @property
    def open_devices(self) -> int:
        with self.lock:
            return self.opens - self.releases


class FakeDevice:
    def __init__(self, src: FakeSource) -> None:
        self.src = src
        self.released = False

    def read(self):
        self.src.unblocked.wait(5)
        if not self.src.cable_connected or not self.src.decodable:
            time.sleep(0.01)
            return False, None
        time.sleep(1.0 / self.src.fps)
        if self.src.scene is not None:
            with self.src.lock:
                self.src.frame_index += 1
                i = self.src.frame_index
            return True, self.src.scene(i)
        return True, np.zeros((self.src.height, self.src.width, 3), np.uint8)

    def nominal_fps(self):
        return self.src.fps

    def rewind(self):
        return False

    def release(self):
        if not self.released:
            self.released = True
            with self.src.lock:
                self.src.releases += 1


class FakeFactory:
    def __init__(self) -> None:
        self.sources: dict[str, FakeSource] = {}

    def add(self, display: str, **kwargs) -> FakeSource:
        self.sources[display] = FakeSource(**kwargs)
        return self.sources[display]

    def open(self, target: CaptureTarget, open_timeout_s: float, read_timeout_s: float):
        src = self.sources.get(target.display)
        if src is None:
            raise SourceError(f"Can't open stream {target.display}", FailureKind.UNREACHABLE)
        if src.open_error is not None:
            raise src.open_error
        if not src.cable_connected:
            raise SourceError(f"Can't open stream {target.display}", FailureKind.UNREACHABLE)
        with src.lock:
            src.opens += 1
        return FakeDevice(src)


def fast_stream_settings() -> StreamSettings:
    return StreamSettings(
        open_timeout_s=1,
        read_timeout_s=1,
        stale_after_s=1,
        reconnect_initial_s=0.1,
        reconnect_max_s=1,
        frame_queue_size=4,
        status_persist_interval_s=5,
    )


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def make_token(perms: list[str], role: str = "operator", subject: str = "user-1", minutes: int = 5) -> str:
    now = datetime.now(UTC)
    payload = {"sub": subject, "role": role, "perms": perms, "type": "access", "iat": now,
               "exp": now + timedelta(minutes=minutes)}  # fmt: skip
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def auth(perms: list[str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(perms or ['*'], role='administrator')}"}


@pytest.fixture
def settings(tmp_path: Path) -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        environment="test",
        data_dir=tmp_path / "data",
        jwt_secret=JWT_SECRET,
        log_json=False,
        log_level="WARNING",
        stream=fast_stream_settings(),
    )


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def probe_results() -> dict[str, ProbeResult]:
    """Per-URL RTSP probe answers; default = probe succeeds."""
    return {}


@pytest.fixture
def manager(settings: AgentSettings, factory: FakeFactory, probe_results) -> Iterator[CameraManager]:
    db = Database(settings.database_path)
    box = SecretBox.from_settings("", settings.secret_key_path)

    def probe(url: str, user: str, pw: str, timeout: float) -> ProbeResult:
        return probe_results.get(url, ProbeResult(True, None, "ok", 200))

    mgr = CameraManager(CameraRepository(db, box), settings, factory=factory, probe=probe)
    yield mgr
    mgr.shutdown(timeout=3)
    db.close()


@pytest.fixture
def client(settings: AgentSettings, manager: CameraManager) -> Iterator[TestClient]:
    app = create_app(settings, manager=manager)
    with TestClient(app) as c:
        yield c
