"""Real OpenCV/FFmpeg decoding (no fakes): generated video files, corrupt files, and —
when available — a real USB camera and a real RTSP camera.

    ACI_TEST_USB_INDEX=0 uv run pytest -m hardware
    ACI_TEST_RTSP_URL=rtsp://user:pass@10.0.0.5/stream uv run pytest -m rtsp
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest

from aci_agent.camera.manager import CameraInput, CameraManager
from aci_agent.camera.models import CameraStatus, FailureKind, SourceType
from aci_agent.camera.sources import OpenCVCaptureFactory
from aci_agent.security.crypto import SecretBox
from aci_agent.storage.database import Database
from aci_agent.storage.repository import CameraRepository
from tests.conftest import wait_for


def make_video(path: Path, frames: int = 60, fps: int = 20, size=(320, 240)) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(frames):
        img = np.zeros((size[1], size[0], 3), np.uint8)
        cv2.rectangle(img, (i * 4 % size[0], 50), (i * 4 % size[0] + 40, 90), (0, 255, 0), -1)
        writer.write(img)
    writer.release()
    return path


@pytest.fixture
def real_manager(settings) -> Iterator[CameraManager]:
    db = Database(settings.database_path)
    mgr = CameraManager(
        CameraRepository(db, SecretBox.from_settings("", settings.secret_key_path)), settings, OpenCVCaptureFactory()
    )
    yield mgr
    mgr.shutdown()
    db.close()


def _rt(mgr: CameraManager, cid: str):
    return mgr.get_camera(cid).runtime


def test_two_video_files_stream_simultaneously(real_manager: CameraManager, tmp_path: Path):
    a = make_video(tmp_path / "a.mp4", fps=20, size=(320, 240))
    b = make_video(tmp_path / "b.mp4", fps=10, size=(640, 360))
    ca = real_manager.create_camera(CameraInput(name="A", source_type=SourceType.FILE, source_uri=str(a))).config
    cb = real_manager.create_camera(CameraInput(name="B", source_type=SourceType.FILE, source_uri=str(b))).config
    assert wait_for(lambda: _rt(real_manager, ca.id).status == CameraStatus.ONLINE, 10)
    assert wait_for(lambda: _rt(real_manager, cb.id).status == CameraStatus.ONLINE, 10)
    assert wait_for(lambda: (_rt(real_manager, ca.id).fps or 0) > 12 and (_rt(real_manager, cb.id).fps or 0) > 6, 6)
    ra, rb = _rt(real_manager, ca.id), _rt(real_manager, cb.id)
    assert (ra.width, ra.height) == (320, 240) and (rb.width, rb.height) == (640, 360)
    # files are paced to real time, not decoded as fast as possible
    assert ra.fps < 30 and rb.fps < 15


def test_file_without_loop_ends_offline(real_manager: CameraManager, tmp_path: Path):
    v = make_video(tmp_path / "short.mp4", frames=10, fps=20)
    cam = real_manager.create_camera(
        CameraInput(name="Short", source_type=SourceType.FILE, source_uri=str(v), options={"loop": False})
    ).config
    assert wait_for(lambda: _rt(real_manager, cam.id).frames_received >= 10, 10)
    assert wait_for(lambda: _rt(real_manager, cam.id).status == CameraStatus.OFFLINE, 5)
    assert _rt(real_manager, cam.id).last_error == "End of video file"


def test_corrupt_video_is_unsupported_format(real_manager: CameraManager, tmp_path: Path):
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(os.urandom(4096))
    result = real_manager.test_input(CameraInput(name="x", source_type=SourceType.FILE, source_uri=str(bad)))
    assert not result.ok and result.failure == FailureKind.UNSUPPORTED_FORMAT


def test_missing_file_is_not_found(real_manager: CameraManager, tmp_path: Path):
    result = real_manager.test_input(
        CameraInput(name="x", source_type=SourceType.FILE, source_uri=str(tmp_path / "nope.mp4"))
    )
    assert not result.ok and result.failure == FailureKind.NOT_FOUND


def test_unreachable_rtsp_with_real_ffmpeg(real_manager: CameraManager):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = real_manager.test_input(
        CameraInput(name="x", source_type=SourceType.RTSP, source_uri=f"rtsp://127.0.0.1:{port}/live")
    )
    assert not result.ok and result.failure == FailureKind.UNREACHABLE


@pytest.mark.hardware
@pytest.mark.skipif("ACI_TEST_USB_INDEX" not in os.environ, reason="set ACI_TEST_USB_INDEX to test a real USB camera")
def test_real_usb_camera_with_file_simultaneously(real_manager: CameraManager, tmp_path: Path):
    usb = real_manager.create_camera(
        CameraInput(name="Webcam", source_type=SourceType.USB, source_uri=os.environ["ACI_TEST_USB_INDEX"])
    ).config
    video = make_video(tmp_path / "v.mp4", frames=200)
    f = real_manager.create_camera(CameraInput(name="File", source_type=SourceType.FILE, source_uri=str(video))).config
    assert wait_for(lambda: _rt(real_manager, usb.id).status == CameraStatus.ONLINE, 15)
    assert wait_for(lambda: _rt(real_manager, f.id).status == CameraStatus.ONLINE, 10)
    assert wait_for(lambda: (_rt(real_manager, usb.id).fps or 0) > 1, 5)
    real_manager.disconnect(usb.id)
    # the device is free again: it can be opened by a fresh test right away
    assert real_manager.test_existing(usb.id).ok
    assert _rt(real_manager, f.id).status == CameraStatus.ONLINE


@pytest.mark.rtsp
@pytest.mark.skipif("ACI_TEST_RTSP_URL" not in os.environ, reason="set ACI_TEST_RTSP_URL to test a real RTSP camera")
def test_real_rtsp_camera(real_manager: CameraManager):
    cam = real_manager.create_camera(
        CameraInput(name="RTSP", source_type=SourceType.RTSP, source_uri=os.environ["ACI_TEST_RTSP_URL"])
    ).config
    assert wait_for(lambda: _rt(real_manager, cam.id).status == CameraStatus.ONLINE, 20)
