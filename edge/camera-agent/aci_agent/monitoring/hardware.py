"""Live hardware metrics. Only values the platform actually exposes are reported; anything else is
``None`` with a reason — never an estimate.

* CPU / RAM / disk / network: psutil (all platforms).
* Temperature: psutil sensors (Linux) or ``/sys/class/thermal`` (Linux, Jetson). Not available on
  Windows without vendor tools.
* GPU: ``nvidia-smi`` (desktop NVIDIA) or Jetson's ``/sys/devices/**/gpu.0/load``.
"""

from __future__ import annotations

import glob
import threading
import time
from pathlib import Path

import psutil

from aci_agent.ai_engine.monitoring.metrics import gpu_status


def _temperatures() -> dict:
    readings: dict[str, float] = {}
    if hasattr(psutil, "sensors_temperatures"):
        try:
            for name, entries in (psutil.sensors_temperatures() or {}).items():
                for e in entries:
                    if e.current and 0 < e.current < 150:
                        readings[f"{name}{'/' + e.label if e.label else ''}"] = round(float(e.current), 1)
        except (OSError, AttributeError):
            pass
    if not readings:
        for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
            try:
                kind = Path(zone, "type").read_text().strip()
                value = int(Path(zone, "temp").read_text().strip()) / 1000
            except (OSError, ValueError):
                continue
            if 0 < value < 150:
                readings[kind] = round(value, 1)
    if not readings:
        return {"available": False, "detail": "No temperature sensors exposed on this platform"}
    return {"available": True, "max_c": max(readings.values()), "sensors": readings}


def _jetson_gpu() -> dict | None:
    for path in (
        "/sys/devices/gpu.0/load",
        "/sys/devices/platform/gpu.0/load",
        *glob.glob("/sys/devices/platform/*/gpu.0/load"),
    ):
        try:
            load = int(Path(path).read_text().strip()) / 10  # per-mille → %
        except (OSError, ValueError):
            continue
        return {"available": True, "name": "Jetson integrated GPU", "utilization_percent": round(load, 1)}
    return None


class HardwareMonitor:
    def __init__(self, data_dir: Path, min_interval_s: float = 2.0) -> None:
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()
        self._last: dict | None = None
        self._last_at = 0.0
        self._net_prev: tuple[float, int, int] | None = None
        self._min_interval = min_interval_s
        psutil.cpu_percent(None)

    def _network(self) -> dict:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        up = []
        for name, st in stats.items():
            if not st.isup or name.lower().startswith(("lo", "loopback")):
                continue
            ipv4 = [a.address for a in addrs.get(name, []) if a.family == 2]
            if ipv4:
                up.append({"interface": name, "ipv4": ipv4, "speed_mbps": st.speed or None})
        io = psutil.net_io_counters()
        now = time.monotonic()
        rate = None
        if self._net_prev is not None:
            dt = max(1e-3, now - self._net_prev[0])
            rate = {
                "rx_kbps": round((io.bytes_recv - self._net_prev[1]) * 8 / 1000 / dt, 1),
                "tx_kbps": round((io.bytes_sent - self._net_prev[2]) * 8 / 1000 / dt, 1),
            }
        self._net_prev = (now, io.bytes_recv, io.bytes_sent)
        return {"connected": bool(up), "interfaces": up, "throughput": rate}

    def sample(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._last is not None and now - self._last_at < self._min_interval:
                return dict(self._last)
            mem = psutil.virtual_memory()
            try:
                disk = psutil.disk_usage(str(self.data_dir if self.data_dir.exists() else Path.cwd()))
                disk_info = {
                    "path": str(self.data_dir),
                    "total_gb": round(disk.total / 1e9, 1),
                    "free_gb": round(disk.free / 1e9, 1),
                    "used_percent": disk.percent,
                }
            except OSError as exc:
                disk_info = {"available": False, "detail": str(exc)[:120]}
            gpu = _jetson_gpu() or gpu_status()
            temperature = _temperatures()
            if not temperature.get("available") and gpu.get("temperature_c") is not None:
                temperature = {
                    "available": True,
                    "max_c": gpu["temperature_c"],
                    "sensors": {"gpu": gpu["temperature_c"]},
                }
            self._last = {
                "cpu": {"percent": psutil.cpu_percent(None), "cores": psutil.cpu_count()},
                "memory": {
                    "total_mb": round(mem.total / 1_048_576),
                    "used_mb": round((mem.total - mem.available) / 1_048_576),
                    "percent": mem.percent,
                },
                "disk": disk_info,
                "temperature": temperature,
                "gpu": gpu,
                "network": self._network(),
                "uptime_s": round(time.time() - psutil.boot_time()),
            }
            self._last_at = now
            return dict(self._last)
