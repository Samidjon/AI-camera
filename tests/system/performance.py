"""MVP 09 performance measurement: 1 / 5 / 10 / 20 cameras, standard and AI mode, on THIS machine.

    cd edge/camera-agent
    uv run python ../../tests/system/performance.py [--cameras 1,5,10,20] [--modes standard,ai] [--window 60]

Each configuration runs a fresh real stack (stack.py): central server + edge agent, N file-source
cameras looping the same real 640×360 @ 15 fps video, warm-up, then a measurement window. Measured:

* camera FPS (received frames, per camera: mean / min)       * agent CPU (process tree) and RAM (RSS)
* GPU utilisation (nvidia-smi, if an NVIDIA GPU exists)      * upload bandwidth agent → server (TCP proxy)
* AI inference FPS and latency (avg / p95)                    * storage growth (events DB) and events/min
* event processing latency: occurred_at → stored on the central server (median / p95)

Nothing is extrapolated: the report contains only what was measured here, with the hardware it ran on.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack import ROOT, Stack, wait_until  # noqa: E402

RESULTS = ROOT / "tests" / "system" / "results"


def hardware() -> dict:
    cpu = platform.processor()
    if os.name == "nt":
        try:
            cpu = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Processor).Name"], capture_output=True, text=True,
                                 timeout=20).stdout.strip() or cpu  # fmt: skip
        except Exception:  # noqa: BLE001
            pass
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": cpu,
        "logical_cores": psutil.cpu_count(),
        "physical_cores": psutil.cpu_count(logical=False),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "gpu": gpu_name(),
        "python": platform.python_version(),
    }


def gpu_name() -> str | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        return subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or None  # fmt: skip
    except Exception:  # noqa: BLE001
        return None


def gpu_util() -> float | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout  # fmt: skip
        return float(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


def tree(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1)))], 1)


def run_config(n: int, mode: str, window: float, warmup: float) -> dict:
    s = Stack(ROOT / "tests" / "system" / ".data" / f"perf-{mode}-{n}", proxy=True)
    try:
        s.start_server(fresh=True)
        s.start_agent(s.enrollment_token())
        s.add_cameras(n)
        wait_until(lambda: sum(c["status"] == "online" for c in s.cameras()) == n, 120, what=f"{n} cameras online")
        if mode == "ai":
            s.agent.post("/api/v1/ai/enable")
            wait_until(lambda: s.agent.get("/api/v1/ai/status")["mode"] == "active", 120, what="AI active")
        time.sleep(warmup)

        known: dict[int, psutil.Process] = {}  # keep Process objects: cpu_percent() needs the previous sample

        def procs_now() -> list[psutil.Process]:
            for p in tree(s.agent_proc.pid):
                if p.pid not in known:
                    known[p.pid] = p
                    p.cpu_percent(None)
            return list(known.values())

        procs_now()
        up0, down0 = s.proxy.counters()
        used0 = float(s.status()["events"]["db_used_mb"])
        ev0 = s.local_event_count()
        t_start = datetime.now(UTC)
        t0 = time.monotonic()
        cpu, rss, fps_mean, fps_min, gpu = [], [], [], [], []
        paused = False
        while time.monotonic() - t0 < window:
            time.sleep(3)
            procs = procs_now()
            total_cpu = 0.0
            total_rss = 0.0
            for p in procs:
                try:
                    total_cpu += p.cpu_percent(None)
                    total_rss += p.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            cpu.append(total_cpu)
            rss.append(total_rss / 2**20)
            cams = s.cameras()
            fps = [c["fps"] or 0.0 for c in cams]
            fps_mean.append(statistics.mean(fps))
            fps_min.append(min(fps))
            if (g := gpu_util()) is not None:
                gpu.append(g)
            paused = paused or bool(s.status()["vision"].get("memory_paused"))
        elapsed = time.monotonic() - t0
        up1, down1 = s.proxy.counters()
        used1 = float(s.status()["events"]["db_used_mb"])
        ev1 = s.local_event_count()
        ai_st = s.agent.get("/api/v1/ai/status") if mode == "ai" else {}
        ai = ai_st.get("metrics", {}) if ai_st else {}
        ai_backend = (ai_st.get("model") or {}).get("backend") if ai_st else None
        # event processing latency: occurred on the edge → stored on the server (same clock here)
        time.sleep(8)  # let the last batch arrive
        with sqlite3.connect(f"file:{s.server_db}?mode=ro", uri=True) as c:
            rows = c.execute("SELECT occurred_at, received_at FROM camera_events WHERE occurred_at >= ?",
                             (t_start.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()  # fmt: skip
        lat = []
        for occ, rec in rows:
            try:
                a = datetime.fromisoformat(str(occ)).replace(tzinfo=UTC)
                b = datetime.fromisoformat(str(rec)).replace(tzinfo=UTC)
                lat.append((b - a).total_seconds())
            except ValueError:
                continue
        nominal = 15.0
        return {
            "cameras": n,
            "mode": mode,
            "window_s": round(elapsed),
            "fps_per_camera_mean": round(statistics.mean(fps_mean), 2),
            "fps_per_camera_min": round(min(fps_min), 2),
            "fps_source": nominal,
            "cpu_percent_of_one_core_mean": round(statistics.mean(cpu), 1),
            "cpu_percent_of_one_core_p95": pct(cpu, 0.95),
            "cpu_percent_of_machine_mean": round(statistics.mean(cpu) / psutil.cpu_count(), 1),
            "ram_mb_mean": round(statistics.mean(rss)),
            "ram_mb_max": round(max(rss)),
            "gpu_util_percent_mean_system_wide": round(statistics.mean(gpu), 1) if gpu else None,
            "ai_backend": ai_backend,
            "upload_kbit_s": round((up1 - up0) * 8 / 1000 / elapsed, 1),
            "download_kbit_s": round((down1 - down0) * 8 / 1000 / elapsed, 1),
            "events_per_min": round((ev1 - ev0) * 60 / elapsed, 1),
            "event_db_growth_mb_per_hour": round((used1 - used0) * 3600 / elapsed, 2),
            "ai_inference_fps": ai.get("inference_fps"),
            "ai_latency_avg_ms": ai.get("latency_avg_ms"),
            "ai_latency_p95_ms": ai.get("latency_p95_ms"),
            "ai_timeouts": ai.get("timeouts"),
            "event_latency_median_s": round(statistics.median(lat), 2) if lat else None,
            "event_latency_p95_s": pct(lat, 0.95),
            "events_measured_for_latency": len(lat),
            "memory_guard_paused_analysis": paused,
        }
    finally:
        s.stop_all()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameras", default="1,5,10,20")
    ap.add_argument("--modes", default="standard,ai")
    ap.add_argument("--window", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=20)
    args = ap.parse_args()
    started = datetime.now(UTC)
    hw = hardware()
    print(json.dumps(hw, indent=1))
    results = []
    for mode in args.modes.split(","):
        for n in (int(x) for x in args.cameras.split(",")):
            print(f"--- {mode}, {n} camera(s)")
            try:
                r = run_config(n, mode, args.window, args.warmup)
            except Exception as exc:  # noqa: BLE001 - record and continue with the next configuration
                r = {"cameras": n, "mode": mode, "error": f"{type(exc).__name__}: {exc}"[:300]}
            print(json.dumps(r))
            results.append(r)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"performance-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps({"started": started.isoformat(), "hardware": hw, "video": "640x360 @ 15 fps (demo-parking.mp4, looped)",
                               "results": results}, indent=2), encoding="utf-8")  # fmt: skip
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
