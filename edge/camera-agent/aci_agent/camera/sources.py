"""Source adapters: validate user input, resolve what to open, and open it through OpenCV/FFmpeg.

Vendor independence comes from standard protocols only: RTSP, ONVIF (stream URI discovery),
UVC/USB (DirectShow / V4L2 / AVFoundation via OpenCV), files and any FFmpeg-readable URL.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import numpy as np

from aci_agent.camera.models import CameraConfig, CameraSecret, FailureKind, SourceType
from aci_agent.camera.onvif import OnvifClient, OnvifError

FFMPEG_SCHEMES = {"rtsp", "rtsps", "rtmp", "rtmps", "http", "https", "udp", "rtp", "srt", "tcp"}
VIDEO_FILE_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".ts", ".mjpeg", ".mjpg", ".h264", ".h265"}

# RTSP over TCP is far more reliable than UDP through NAT/Wi-Fi and avoids smeared frames.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


class SourceError(Exception):
    def __init__(self, message: str, failure: FailureKind) -> None:
        super().__init__(message)
        self.failure = failure


# --------------------------------------------------------------------------- input validation


def split_credentials(url: str) -> tuple[str, str, str]:
    """``rtsp://user:pass@host/p`` → (``rtsp://host/p``, user, pass). Credentials never stay in the URL."""
    parts = urlsplit(url)
    if not parts.username and not parts.password:
        return url, "", ""
    host = parts.hostname or ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    clean = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return clean, unquote(parts.username or ""), unquote(parts.password or "")


def validate_source(source_type: SourceType, source_uri: str) -> str:
    """Return the normalized source string or raise ValueError with an operator-friendly message."""
    value = source_uri.strip()
    if not value:
        raise ValueError("Source is required")
    if len(value) > 1024:
        raise ValueError("Source is too long")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("Source contains control characters")

    if source_type in (SourceType.RTSP, SourceType.FFMPEG):
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if source_type == SourceType.RTSP and scheme not in {"rtsp", "rtsps"}:
            raise ValueError("RTSP source must start with rtsp:// or rtsps://")
        if source_type == SourceType.FFMPEG and scheme not in FFMPEG_SCHEMES:
            raise ValueError(f"Unsupported stream scheme '{scheme}'. Allowed: {', '.join(sorted(FFMPEG_SCHEMES))}")
        if not parts.hostname:
            raise ValueError("Stream URL must contain a host")
        if parts.port is not None and not 0 < parts.port < 65536:
            raise ValueError("Invalid port")
        return value

    if source_type == SourceType.ONVIF:
        match = re.fullmatch(r"(?P<host>[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:]+\])(:(?P<port>\d{1,5}))?", value)
        if not match or (match["port"] and not 0 < int(match["port"]) < 65536):
            raise ValueError("ONVIF source must be host or host:port (e.g. 192.168.1.64:80)")
        return value

    if source_type == SourceType.USB:
        if not value.isdigit() or not 0 <= int(value) <= 63:
            raise ValueError("USB source must be a device index 0–63")
        return str(int(value))

    if source_type == SourceType.FILE:
        path = Path(value)
        if path.suffix.lower() not in VIDEO_FILE_SUFFIXES:
            raise ValueError(f"Unsupported video file type '{path.suffix}'")
        return str(path)

    raise ValueError(f"Unknown source type {source_type}")


def inject_credentials(url: str, secret: CameraSecret | None) -> str:
    if not secret:
        return url
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(secret.username, safe='')}:{quote(secret.password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# --------------------------------------------------------------------------- capture abstraction


@dataclass(slots=True)
class CaptureTarget:
    source_type: SourceType
    uri: str  # what OpenCV opens; MAY contain credentials — never log or return it
    display: str  # safe to show / log
    loop: bool = False  # files only
    probe_url: str | None = None  # credential-free rtsp:// URL used for RTSP diagnostics


class CaptureDevice(Protocol):
    def read(self) -> tuple[bool, np.ndarray | None]: ...
    def nominal_fps(self) -> float | None: ...
    def rewind(self) -> bool: ...
    def release(self) -> None: ...


class CaptureFactory(Protocol):
    def open(self, target: CaptureTarget, open_timeout_s: float, read_timeout_s: float) -> CaptureDevice: ...


class OpenCVCapture:
    def __init__(self, cap) -> None:  # cap: cv2.VideoCapture
        self._cap = cap

    def read(self) -> tuple[bool, np.ndarray | None]:
        ok, frame = self._cap.read()
        return (bool(ok) and frame is not None and frame.size > 0), frame

    def nominal_fps(self) -> float | None:
        import cv2

        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0)
        return fps if 0 < fps < 240 else None

    def rewind(self) -> bool:
        import cv2

        return bool(self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0))

    def release(self) -> None:
        self._cap.release()


class OpenCVCaptureFactory:
    """Opens sources with OpenCV. Network/file sources use the FFmpeg backend with timeouts."""

    def open(self, target: CaptureTarget, open_timeout_s: float, read_timeout_s: float) -> CaptureDevice:
        import cv2

        if target.source_type == SourceType.USB:
            index = int(target.uri)
            backend = (
                cv2.CAP_DSHOW
                if sys.platform == "win32"
                else cv2.CAP_V4L2
                if sys.platform.startswith("linux")
                else cv2.CAP_ANY
            )
            cap = cv2.VideoCapture(index, backend)
            if not cap.isOpened():
                cap.release()
                raise SourceError(f"USB camera #{index} is not available (unplugged or in use)", FailureKind.NOT_FOUND)
            return OpenCVCapture(cap)

        if target.source_type == SourceType.FILE:
            if not Path(target.uri).is_file():
                raise SourceError(f"Video file not found: {target.display}", FailureKind.NOT_FOUND)
            cap = cv2.VideoCapture(target.uri, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                raise SourceError(f"Video file can't be decoded: {target.display}", FailureKind.UNSUPPORTED_FORMAT)
            return OpenCVCapture(cap)

        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(open_timeout_s * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(read_timeout_s * 1000),
        ]  # fmt: skip
        cap = cv2.VideoCapture(target.uri, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            raise SourceError(f"Can't open stream {target.display}", FailureKind.UNREACHABLE)
        return OpenCVCapture(cap)


def resolve_target(config: CameraConfig, secret: CameraSecret | None, timeout_s: float = 5.0) -> CaptureTarget:
    """Turn stored config + decrypted secret into an openable target. ONVIF is resolved here (network)."""
    st = config.source_type
    if st in (SourceType.RTSP, SourceType.FFMPEG):
        probe = config.source_uri if urlsplit(config.source_uri).scheme.lower() == "rtsp" else None
        return CaptureTarget(st, inject_credentials(config.source_uri, secret), config.source_uri, probe_url=probe)
    if st == SourceType.ONVIF:
        host, _, port = (
            config.source_uri.rpartition(":") if config.source_uri.count(":") == 1 else (config.source_uri, "", "")
        )
        client = OnvifClient(
            host or config.source_uri,
            int(port) if port else 80,
            secret.username if secret else "",
            secret.password if secret else "",
            timeout=timeout_s,
        )
        try:
            uri = client.stream_uri(int(config.options.get("onvif_profile", 0)))
        except OnvifError as exc:
            raise SourceError(str(exc), exc.failure) from exc
        finally:
            client.close()
        clean, _, _ = split_credentials(uri)
        return CaptureTarget(
            SourceType.RTSP,
            inject_credentials(clean, secret),
            f"onvif://{config.source_uri} → {clean}",
            probe_url=clean,
        )
    if st == SourceType.USB:
        return CaptureTarget(st, config.source_uri, f"usb:{config.source_uri}")
    if st == SourceType.FILE:
        return CaptureTarget(st, config.source_uri, config.source_uri, loop=bool(config.options.get("loop", True)))
    raise SourceError(f"Unsupported source type {st}", FailureKind.INVALID_SOURCE)
