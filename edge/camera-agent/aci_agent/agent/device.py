"""Device identity and static hardware / OS information.

The device ID is a random UUID generated on first start and stored in the local database: stable
across restarts and updates, unique per installation, not derived from hardware serials.
"""

from __future__ import annotations

import os
import platform
import socket
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import cv2
import psutil

from aci_agent import __version__
from aci_agent.storage.database import Database


class DeviceIdentity:
    def __init__(self, db: Database, default_name: str | None = None) -> None:
        self._db = db
        rows = db.query("SELECT device_id, device_name FROM device WHERE id = 1")
        if rows:
            self.device_id, self.device_name = rows[0]["device_id"], rows[0]["device_name"]
        else:
            self.device_id = str(uuid.uuid4())
            self.device_name = (default_name or socket.gethostname() or "edge-device")[:120]
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO device (id, device_id, device_name, created_at) VALUES (1, ?, ?, ?)",
                    (self.device_id, self.device_name, datetime.now(UTC).isoformat()),
                )

    def rename(self, name: str) -> None:
        name = name.strip()
        if not 1 <= len(name) <= 120:
            raise ValueError("Device name must be 1–120 characters")
        with self._db.transaction() as conn:
            conn.execute("UPDATE device SET device_name = ? WHERE id = 1", (name,))
        self.device_name = name


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore").strip("\x00\n ") or None
    except OSError:
        return None


def _cpu_model() -> str | None:
    if platform.system() == "Linux":
        info = _read("/proc/cpuinfo") or ""
        for key in ("model name", "Hardware", "Model"):
            for line in info.splitlines():
                if line.lower().startswith(key.lower()) and ":" in line:
                    return line.split(":", 1)[1].strip()
    return platform.processor() or None


@lru_cache(maxsize=1)
def hardware_info() -> dict:
    """Static facts about the machine (cached)."""
    jetson = _read("/proc/device-tree/model")
    mem = psutil.virtual_memory()
    return {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version()[:120],
        "platform": platform.platform()[:160],
        "architecture": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_cores_logical": psutil.cpu_count() or None,
        "cpu_cores_physical": psutil.cpu_count(logical=False) or None,
        "memory_total_mb": round(mem.total / 1_048_576),
        "board_model": jetson,  # e.g. "NVIDIA Jetson Orin Nano Developer Kit" (Linux device tree)
        "is_jetson": bool(jetson and "jetson" in jetson.lower()),
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "opencv_cuda": bool(hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0),
        "agent_version": __version__,
        "pid": os.getpid(),
    }
