"""Inference metrics and GPU probing. Only measured / reported values; nothing is estimated."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections import deque


class InferenceMetrics:
    def __init__(self, window: int = 120) -> None:
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=window)
        self._times: deque[float] = deque(maxlen=window)
        self.inferences = 0
        self.detections = 0
        self.timeouts = 0
        self.errors = 0

    def record(self, latency_ms: float, detections: int) -> None:
        with self._lock:
            self._latencies.append(latency_ms)
            self._times.append(time.monotonic())
            self.inferences += 1
            self.detections += detections

    def snapshot(self) -> dict:
        with self._lock:
            lat = sorted(self._latencies)
            times = list(self._times)
        fps = None
        if len(times) >= 2 and times[-1] > times[0] and time.monotonic() - times[-1] < 5:
            fps = round((len(times) - 1) / (times[-1] - times[0]), 2)
        return {
            "inference_fps": fps,
            "latency_avg_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "latency_p95_ms": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "inferences": self.inferences,
            "detections": self.detections,
            "timeouts": self.timeouts,
            "errors": self.errors,
        }


def gpu_status() -> dict:
    """NVIDIA GPU utilisation via nvidia-smi when present; otherwise reported as unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "detail": "No NVIDIA GPU / driver detected (nvidia-smi not found)"}
    try:
        out = (
            subprocess.run(
                [
                    exe,
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            .stdout.strip()
            .splitlines()
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"available": False, "detail": f"nvidia-smi failed: {type(exc).__name__}"}
    if not out:
        return {"available": False, "detail": "nvidia-smi reported no GPUs"}
    name, util, used, total, temp = (v.strip() for v in out[0].split(","))
    return {
        "available": True,
        "name": name,
        "utilization_percent": float(util),
        "memory_used_mb": float(used),
        "memory_total_mb": float(total),
        "temperature_c": float(temp),
    }
